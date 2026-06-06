"""Tests for the submission API routes (Task 7, Implementation.md §3).

Every endpoint is exercised against an isolated temp DB using FastAPI's
TestClient. The monkeypatch + importlib.reload pattern (mirroring test_db.py)
points DB_PATH at a per-test SQLite file before the app and db modules load, so
the suite never touches the real database.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient bound to a fresh temp DB with the schema initialised."""
    db_file = tmp_path / "test_app.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    # Reload config + db so the patched DB_PATH is picked up, then the route
    # module + app so they bind to the reloaded db.
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import services.audit_service as audit_service
    importlib.reload(audit_service)
    import backend.routes.submissions as submissions
    importlib.reload(submissions)
    import backend.main as main
    importlib.reload(main)

    db_module.init_db()

    with TestClient(main.app) as test_client:
        yield test_client, db_module


VALID_FORM = {
    "legal_name": "Acme Technologies Private Limited",
    "pan": "ABCDE1234F",
    "gst": "27ABCDE1234F1Z5",
    "address": "1 Industrial Estate, Pune",
    "contact_email": "ops@acme.example",
    "contact_phone": "+919999999999",
    "vendor_type": "company",
    "bank": {
        "account_number": "123456789012",
        "ifsc": "HDFC0001234",
        "account_holder": "Acme Technologies Private Limited",
    },
}


def test_submissions_route_is_registered(client):
    test_client, _ = client
    paths = {route.path for route in test_client.app.routes}
    assert "/submissions" in paths
    assert "/submission/{submission_id}" in paths


def test_post_creates_submission_and_writes_audit(client):
    test_client, db_module = client

    resp = test_client.post("/submissions", json=VALID_FORM)
    assert resp.status_code == 200
    body = resp.json()
    submission_id = body["submission_id"]
    assert submission_id
    assert body["status"] == "received"

    # A submissions row exists with status 'received'.
    row = db_module.get_submission(submission_id)
    assert row is not None
    assert row["status"] == "received"

    # A SUBMISSION_CREATED audit row was written with the form summary.
    audit_rows = db_module.query(
        "SELECT * FROM audit_logs WHERE submission_id = ? AND action = ?",
        [submission_id, "SUBMISSION_CREATED"],
    )
    assert len(audit_rows) == 1
    import json

    payload = json.loads(audit_rows[0]["payload_json"])
    assert payload["legal_name"] == VALID_FORM["legal_name"]
    assert payload["pan"] == VALID_FORM["pan"]
    assert payload["gst"] == VALID_FORM["gst"]


def test_get_submissions_lists_created_submission(client):
    test_client, _ = client

    created = test_client.post("/submissions", json=VALID_FORM).json()

    resp = test_client.get("/submissions")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)

    match = next(i for i in items if i["submission_id"] == created["submission_id"])
    assert match["legal_name"] == VALID_FORM["legal_name"]
    assert match["status"] == "received"
    assert match["created_at"]
    # No decision yet -> decision fields are null.
    assert match["decision_status"] is None
    assert match["fraud_risk_score"] is None


def test_get_submission_detail_returns_full_composition(client):
    test_client, db_module = client

    submission_id = test_client.post("/submissions", json=VALID_FORM).json()[
        "submission_id"
    ]

    # Seed a decision + communication so the detail composition is exercised.
    scores = {
        "completeness_score": 100.0,
        "consistency_score": 100.0,
        "compliance_score": 100.0,
        "fraud_risk_score": 0.0,
    }
    decision_id = db_module.insert_decision(
        submission_id, scores, "approved", {"summary": "looks good"}
    )
    db_module.insert_communication(
        decision_id,
        {"subject": "Welcome", "body": "Hi", "requested_items": ["nothing"]},
    )

    resp = test_client.get(f"/submission/{submission_id}")
    assert resp.status_code == 200
    detail = resp.json()

    assert detail["submission_id"] == submission_id
    assert detail["form"]["legal_name"] == VALID_FORM["legal_name"]
    assert detail["form"]["bank"]["ifsc"] == VALID_FORM["bank"]["ifsc"]
    assert detail["documents"] == []
    assert detail["validation_results"] == []
    assert detail["decision"]["final_status"] == "approved"
    assert detail["decision"]["explanation"] == {"summary": "looks good"}
    assert len(detail["communications"]) == 1
    assert detail["communications"][0]["requested_items"] == ["nothing"]


def test_get_unknown_submission_returns_404(client):
    test_client, _ = client
    resp = test_client.get("/submission/does-not-exist")
    assert resp.status_code == 404
