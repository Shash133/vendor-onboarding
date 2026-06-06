"""Tests for the decision API routes (Task 13, Implementation.md §3).

Validates: Requirements 6.1, 8.3, 10.3.

Exercises ``GET /decision/{id}`` (after a real workflow run), the 404 path, and
``POST /decision/{id}/override`` (updates status + writes a DECISION_OVERRIDDEN
audit row). Runs against an isolated temp DB using the standard reload pattern;
no API key is needed (agents use deterministic fallbacks).
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

# A clean vendor that passes every rule → approved (mirrors test_workflow.py).
_CLEAN_PAN = "ABCCE1234F"
_CLEAN_GST = "27ABCCE1234F1Z2"
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


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_decisions.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    import backend.main as main
    importlib.reload(main)

    with TestClient(main.app) as test_client:
        test_client.db = db_module
        yield test_client


def _create_clean_submission(db_module) -> str:
    submission_id = db_module.new_id()
    db_module.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at) VALUES (?,?,?,?)",
        [submission_id, json.dumps(_CLEAN_FORM), "received", db_module.utcnow_iso()],
    )
    for slot, doc_type, extracted in _CLEAN_DOCS:
        document_id = db_module.new_id()
        db_module.execute(
            "INSERT INTO documents("
            "document_id, submission_id, slot, file_path, doc_type, classify_conf, "
            "extracted_json, legible, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                document_id, submission_id, slot,
                f"{submission_id}/{document_id}.pdf", doc_type, 0.95,
                json.dumps(extracted), 1, db_module.utcnow_iso(),
            ],
        )
    return submission_id


def test_decision_route_is_registered(client):
    paths = {route.path for route in client.app.routes}
    assert "/decision/{submission_id}" in paths
    assert "/decision/{submission_id}/override" in paths


def test_get_decision_after_workflow_run(client):
    db_module = client.db
    submission_id = _create_clean_submission(db_module)
    client.post("/workflow/run", json={"submission_id": submission_id})

    resp = client.get(f"/decision/{submission_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_status"] == "approved"
    assert body["completeness_score"] == 100.0
    assert body["fraud_risk_score"] == 0.0
    # Explanation is generated for every decision (Requirement 6.1).
    assert body["explanation"] is not None
    assert "summary" in body["explanation"]
    # rule_results are read back from persisted validation_results (all 28).
    assert len(body["rule_results"]) == 28


def test_get_decision_404_when_absent(client):
    resp = client.get("/decision/does-not-exist")
    assert resp.status_code == 404


def test_override_updates_status_and_writes_audit(client):
    db_module = client.db
    submission_id = _create_clean_submission(db_module)
    client.post("/workflow/run", json={"submission_id": submission_id})

    resp = client.post(
        f"/decision/{submission_id}/override",
        json={"new_status": "rejected", "note": "Manual review: suspicious activity"},
    )
    assert resp.status_code == 200
    assert resp.json()["final_status"] == "rejected"

    # The decisions row is updated (overridden + new status + note).
    decision = db_module.get_decision(submission_id)
    assert decision["final_status"] == "rejected"
    assert decision["overridden"] == 1
    assert decision["override_note"] == "Manual review: suspicious activity"

    # A DECISION_OVERRIDDEN audit row was written by the reviewer actor.
    rows = db_module.query(
        "SELECT * FROM audit_logs WHERE submission_id = ? AND action = ?",
        [submission_id, "DECISION_OVERRIDDEN"],
    )
    assert len(rows) == 1
    assert rows[0]["actor"] == "reviewer"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["old_status"] == "approved"
    assert payload["new_status"] == "rejected"


def test_override_404_when_no_decision(client):
    db_module = client.db
    submission_id = _create_clean_submission(db_module)  # no workflow run → no decision
    resp = client.post(
        f"/decision/{submission_id}/override",
        json={"new_status": "approved", "note": "n/a"},
    )
    assert resp.status_code == 404
