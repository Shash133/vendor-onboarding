"""Tests for the SSE workflow stream route (Task 15, Implementation.md §3/§6).

``GET /workflow/stream/{id}`` runs the pipeline in a worker thread and relays a
``StageEvent`` per stage to the client over Server-Sent Events, finishing with a
terminal ``complete`` event that carries the final decision status.

Each test runs against an isolated temp DB using the same monkeypatch +
importlib.reload pattern as test_submissions.py, so the suite never touches the
real database. FastAPI's TestClient buffers the streamed response body, which we
parse into discrete SSE events for assertions.

Covers Requirement 2.3 (live per-stage streaming) and Requirement 10.3 (every
endpoint has a test).
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

# Exact stage order emitted by the workflow engine (Implementation.md §6).
EXPECTED_STAGES = [
    "upload",
    "classify",
    "extract",
    "validate",
    "consistency",
    "risk",
    "decide",
]


@pytest.fixture(autouse=True)
def _reset_sse_app_status():
    """Reset sse-starlette's module-level exit Event between tests.

    ``EventSourceResponse`` lazily creates ``AppStatus.should_exit_event`` and
    binds it to the event loop of the first streaming request. TestClient runs
    each request on a fresh event loop, so a stale Event left over from a prior
    request would be bound to a dead loop and raise "bound to a different event
    loop". Clearing it before each test forces a fresh, correctly-bound Event.
    """
    import sse_starlette.sse as sse

    sse.AppStatus.should_exit_event = None
    yield
    sse.AppStatus.should_exit_event = None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient bound to a fresh temp DB with the schema initialised."""
    db_file = tmp_path / "test_app.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    # Reload config + db so the patched DB_PATH is picked up, then the service +
    # route modules + app so they bind to the reloaded db.
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import services.audit_service as audit_service
    importlib.reload(audit_service)
    import services.workflow_engine as workflow_engine
    importlib.reload(workflow_engine)
    import backend.routes.workflow as workflow_route
    importlib.reload(workflow_route)
    import backend.main as main
    importlib.reload(main)

    db_module.init_db()

    with TestClient(main.app) as test_client:
        yield test_client, db_module


def _create_submission(db_module) -> str:
    """Insert a clean submission + its 3 classified/extracted documents.

    Mirrors tests/test_workflow.py: the form passes every rule and each document
    carries its ``doc_type`` and a pre-stored flat extraction. ``file_path`` is
    required by the schema but points at a non-existent file, so the agents find
    no bytes, skip the model call, and reuse the injected extraction — keeping
    the run deterministic with no API key.
    """
    pan, name, acct, ifsc = "ABCCE1234F", "Acme Technologies Private Limited", "123456789012", "HDFC0001234"
    form = {
        "legal_name": name,
        "address": "1 MG Road, Bengaluru, Karnataka",
        "contact_email": "vendor@acme.com",
        "contact_phone": "+91 9876543210",
        "vendor_type": "company",
        "pan": pan,
        "gst": "27ABCCE1234F1Z2",
        "bank": {"account_number": acct, "ifsc": ifsc, "account_holder": name},
    }
    docs = [
        ("pan", "PAN_CARD", {"pan": pan, "name": name}),
        ("gst", "GST_CERTIFICATE", {"gstin": "27ABCCE1234F1Z2", "legal_name": name}),
        ("bank", "CANCELLED_CHEQUE", {"account_number": acct, "ifsc": ifsc, "account_holder": name}),
    ]
    sub_id = db_module.new_id()
    db_module.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at)"
        " VALUES (?,?,?,?)",
        [sub_id, json.dumps(form), "received", db_module.utcnow_iso()],
    )
    for slot, doc_type, extracted in docs:
        document_id = db_module.new_id()
        db_module.execute(
            "INSERT INTO documents("
            "document_id, submission_id, slot, file_path, doc_type, classify_conf, "
            "extracted_json, legible, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            [
                document_id,
                sub_id,
                slot,
                f"{sub_id}/{document_id}.pdf",  # non-existent → fallback path
                doc_type,
                0.95,
                json.dumps(extracted),
                1,
                db_module.utcnow_iso(),
            ],
        )
    return sub_id


def _parse_sse(raw: str) -> list[dict]:
    """Parse a raw SSE response body into a list of {event, data} dicts.

    Events are separated by a blank line; each event has ``event:`` and ``data:``
    lines. ``data`` is JSON-decoded when possible.
    """
    events: list[dict] = []
    normalized = raw.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())
        if not data_lines and event_name is None:
            continue
        data_str = "\n".join(data_lines)
        try:
            data = json.loads(data_str) if data_str else None
        except json.JSONDecodeError:
            data = data_str
        events.append({"event": event_name, "data": data})
    return events


def test_stream_route_is_registered(client):
    test_client, _ = client
    paths = {route.path for route in test_client.app.routes}
    assert "/workflow/stream/{submission_id}" in paths


def test_stream_unknown_submission_returns_404(client):
    test_client, _ = client
    resp = test_client.get("/workflow/stream/does-not-exist")
    assert resp.status_code == 404


def test_stream_emits_stage_events_and_terminal_complete(client):
    test_client, db_module = client
    sub_id = _create_submission(db_module)

    resp = test_client.get(f"/workflow/stream/{sub_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    assert events, "expected at least one SSE event"

    # Stage events: collect the stage names that reported 'started'.
    stage_events = [e for e in events if e["event"] == "stage"]
    started_stages = [
        e["data"]["stage"] for e in stage_events if e["data"]["status"] == "started"
    ]
    for stage in EXPECTED_STAGES:
        assert stage in started_stages, f"missing 'started' event for stage '{stage}'"

    # Every started stage also reports an 'ok' (clean skeleton submission).
    ok_stages = [
        e["data"]["stage"] for e in stage_events if e["data"]["status"] == "ok"
    ]
    for stage in EXPECTED_STAGES:
        assert stage in ok_stages, f"missing 'ok' event for stage '{stage}'"

    # Terminal event carries the final decision status.
    assert events[-1]["event"] == "complete"
    assert events[-1]["data"]["submission_id"] == sub_id
    assert events[-1]["data"]["final_status"] == "approved"


def test_stream_stages_arrive_in_pipeline_order(client):
    test_client, db_module = client
    sub_id = _create_submission(db_module)

    resp = test_client.get(f"/workflow/stream/{sub_id}")
    assert resp.status_code == 200

    events = _parse_sse(resp.text)
    started_order = [
        e["data"]["stage"]
        for e in events
        if e["event"] == "stage" and e["data"]["status"] == "started"
    ]
    # The 'started' events must appear in the exact pipeline order.
    assert started_order == EXPECTED_STAGES


def test_stream_persists_workflow_runs_and_decision(client):
    test_client, db_module = client
    sub_id = _create_submission(db_module)

    resp = test_client.get(f"/workflow/stream/{sub_id}")
    assert resp.status_code == 200
    # Drain the body so the worker thread has fully completed.
    _parse_sse(resp.text)

    # The run should have written workflow_runs rows and a decision, and moved
    # the submission to 'decided' — proving the engine actually ran, not just
    # that events were emitted.
    runs = db_module.get_workflow_runs(sub_id)
    run_stages = {r["stage"] for r in runs}
    for stage in EXPECTED_STAGES:
        assert stage in run_stages

    decision = db_module.get_decision(sub_id)
    assert decision is not None
    assert decision["final_status"] == "approved"
    assert db_module.get_submission(sub_id)["status"] == "decided"
