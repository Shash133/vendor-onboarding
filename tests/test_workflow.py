"""Tests for the no-AI workflow engine skeleton (Task 9).

Validates: Requirements 2.1, 2.2, 10.3.

These run the pipeline against an isolated temporary SQLite DB using the standard
monkeypatch + reload pattern (see ``tests/test_db.py``): point ``DB_PATH`` at a
tmp file, reload ``backend.config`` then ``database.db`` so the patched path is
picked up, then drive the app via ``fastapi.testclient.TestClient``.

A clean submission must flow straight through every stage to an ``approved``
decision, leaving behind a decisions row, one ``ok`` workflow_runs row per stage,
the per-stage ``*_OK`` audit events plus ``DECISION_GENERATED``, and the
submission flipped to ``decided``.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

# --- Clean fixture (mirrors tests/test_rules.py) ------------------------------
# A clean vendor that passes all 28 rules. Documents carry their doc_type and a
# pre-stored (flat) extraction so the pipeline is deterministic without an API
# key: file_path points at a non-existent file, so the classify/extract agents
# skip the (unavailable) bytes and the injected extraction is reused.
_CLEAN_PAN = "ABCCE1234F"  # 4th char 'C' = company
_CLEAN_GST = "27ABCCE1234F1Z2"  # 27 | <PAN> | 1Z | checksum '2'
_CLEAN_NAME = "Acme Technologies Private Limited"
_CLEAN_ACCT = "123456789012"
_CLEAN_IFSC = "HDFC0001234"

_CLEAN_FORM = {
    "legal_name": _CLEAN_NAME,
    "address": "1 MG Road, Bengaluru, Karnataka",
    "contact_email": "vendor@acme.com",
    "contact_phone": "+91 9876543210",
    "vendor_type": "company",
    "pan": _CLEAN_PAN,
    "gst": _CLEAN_GST,
    "bank": {"account_number": _CLEAN_ACCT, "ifsc": _CLEAN_IFSC, "account_holder": _CLEAN_NAME},
}

_CLEAN_DOCS = [
    ("pan", "PAN_CARD", {"pan": _CLEAN_PAN, "name": _CLEAN_NAME}),
    ("gst", "GST_CERTIFICATE", {"gstin": _CLEAN_GST, "legal_name": _CLEAN_NAME}),
    ("bank", "CANCELLED_CHEQUE",
     {"account_number": _CLEAN_ACCT, "ifsc": _CLEAN_IFSC, "account_holder": _CLEAN_NAME}),
]

# The stages that run for an approved decision (communicate is skipped).
EXPECTED_STAGES = {
    "upload",
    "classify",
    "extract",
    "validate",
    "consistency",
    "risk",
    "decide",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient bound to an isolated temp DB with the schema initialised."""
    db_file = tmp_path / "test_workflow.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    # Reload config + db so the patched DB_PATH is used everywhere downstream.
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    db_module.init_db()

    # Rebuild the app so its routers + lifespan reference the reloaded db.
    import backend.main as main
    importlib.reload(main)

    with TestClient(main.app) as test_client:
        test_client.db = db_module  # convenience handle for assertions
        yield test_client


def _create_submission(db_module) -> str:
    """Insert a clean submission + its 3 classified/extracted documents.

    The form passes every rule and each document row carries its ``doc_type`` and
    a pre-stored flat extraction. ``file_path`` is required by the schema but
    points at a non-existent file, so the classify/extract agents find no bytes,
    skip the model call, and the injected extraction is reused — keeping the run
    fully deterministic with no API key.
    """
    submission_id = db_module.new_id()
    db_module.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at)"
        " VALUES (?,?,?,?)",
        [submission_id, json.dumps(_CLEAN_FORM), "received", db_module.utcnow_iso()],
    )
    for slot, doc_type, extracted in _CLEAN_DOCS:
        document_id = db_module.new_id()
        db_module.execute(
            "INSERT INTO documents("
            "document_id, submission_id, slot, file_path, doc_type, classify_conf, "
            "extracted_json, legible, created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?)",
            [
                document_id,
                submission_id,
                slot,
                f"{submission_id}/{document_id}.pdf",  # non-existent → fallback path
                doc_type,
                0.95,
                json.dumps(extracted),
                1,
                db_module.utcnow_iso(),
            ],
        )
    return submission_id


def test_run_clean_submission_is_approved(client):
    db_module = client.db
    submission_id = _create_submission(db_module)

    resp = client.post("/workflow/run", json={"submission_id": submission_id})
    assert resp.status_code == 200

    body = resp.json()
    assert body["submission_id"] == submission_id
    decision = body["decision"]
    assert decision["final_status"] == "approved"
    assert decision["completeness_score"] == 100.0
    assert decision["consistency_score"] == 100.0
    assert decision["compliance_score"] == 100.0
    assert decision["fraud_risk_score"] == 0.0
    # Task 11: rule_results is now populated from the real validation engine. A
    # clean submission passes every rule (all 28), so none should be a failure.
    rule_results = decision["rule_results"]
    assert len(rule_results) == 28
    assert all(r["outcome"] == "pass" for r in rule_results), (
        [r for r in rule_results if r["outcome"] != "pass"]
    )


def test_run_persists_decision_and_marks_submission_decided(client):
    db_module = client.db
    submission_id = _create_submission(db_module)

    client.post("/workflow/run", json={"submission_id": submission_id})

    decision = db_module.get_decision(submission_id)
    assert decision is not None
    assert decision["final_status"] == "approved"

    submission = db_module.get_submission(submission_id)
    assert submission["status"] == "decided"


def test_run_writes_ok_workflow_run_row_per_stage(client):
    db_module = client.db
    submission_id = _create_submission(db_module)

    client.post("/workflow/run", json={"submission_id": submission_id})

    runs = db_module.get_workflow_runs(submission_id)
    stages_seen = {r["stage"] for r in runs}
    assert EXPECTED_STAGES.issubset(stages_seen)

    for run in runs:
        if run["stage"] in EXPECTED_STAGES:
            assert run["status"] == "ok"
            assert run["duration_ms"] is not None
            assert run["finished_at"] is not None


def test_run_writes_stage_ok_and_decision_audit_events(client):
    db_module = client.db
    submission_id = _create_submission(db_module)

    client.post("/workflow/run", json={"submission_id": submission_id})

    actions = {row["action"] for row in db_module.query(
        "SELECT action FROM audit_logs WHERE submission_id = ?", [submission_id]
    )}
    for stage in EXPECTED_STAGES:
        assert f"{stage.upper()}_OK" in actions
    assert "DECISION_GENERATED" in actions


def test_run_unknown_submission_returns_404(client):
    resp = client.post("/workflow/run", json={"submission_id": "does-not-exist"})
    assert resp.status_code == 404


def test_engine_emits_stage_events_via_callback(client):
    """The emit callback receives a StageEvent per stage transition."""
    db_module = client.db
    submission_id = _create_submission(db_module)

    from services import workflow_engine

    events = []
    decision = workflow_engine.run(submission_id, emit=events.append)

    assert decision.final_status == "approved"
    # Every run stage emits a 'started' and an 'ok' event.
    started = {e.stage for e in events if e.status == "started"}
    ok = {e.stage for e in events if e.status == "ok"}
    assert EXPECTED_STAGES.issubset(started)
    assert EXPECTED_STAGES.issubset(ok)
