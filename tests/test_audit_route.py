"""Tests for the audit log API route (Task 14, Implementation.md §3, §8).

Exercised against an isolated temp DB using FastAPI's TestClient and the
monkeypatch + importlib.reload pattern (mirroring test_submissions.py), so the
suite never touches the real database.

Validates: Requirements 7.1, 8.1, 10.3
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient bound to a fresh temp DB with the schema initialised."""
    db_file = tmp_path / "test_app.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import services.audit_service as audit_service
    importlib.reload(audit_service)
    import backend.routes.submissions as submissions
    importlib.reload(submissions)
    import backend.routes.audit as audit_route
    importlib.reload(audit_route)
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


def test_audit_route_is_registered(client):
    test_client, _ = client
    paths = {route.path for route in test_client.app.routes}
    assert "/audit/{submission_id}" in paths


def test_get_audit_returns_ordered_events(client):
    test_client, db_module = client

    submission_id = test_client.post("/submissions", json=VALID_FORM).json()[
        "submission_id"
    ]

    # Creating the submission wrote a SUBMISSION_CREATED event. Append a couple
    # more so ordering (oldest-first) can be asserted.
    db_module.execute(
        "UPDATE audit_logs SET created_at = ? WHERE submission_id = ?",
        ["2020-01-01T00:00:00Z", submission_id],
    )
    import services.audit_service as audit_service

    audit_service.log_event(
        submission_id, "agent:classifier", "CLASSIFY_OK", {"doc_type": "PAN_CARD"}
    )
    audit_service.log_event(
        submission_id, "system", "DECISION_GENERATED", {"status": "approved"}
    )

    resp = test_client.get(f"/audit/{submission_id}")
    assert resp.status_code == 200
    events = resp.json()

    actions = [e["action"] for e in events]
    assert actions == ["SUBMISSION_CREATED", "CLASSIFY_OK", "DECISION_GENERATED"]

    # Each entry is AuditEntryOut-shaped with a parsed payload dict.
    first = events[0]
    assert set(first.keys()) == {"actor", "action", "payload", "created_at"}
    assert first["actor"] == "system"
    assert first["payload"]["legal_name"] == VALID_FORM["legal_name"]

    classify = events[1]
    assert classify["actor"] == "agent:classifier"
    assert classify["payload"] == {"doc_type": "PAN_CARD"}


def test_get_audit_unknown_submission_returns_empty_list(client):
    test_client, _ = client
    resp = test_client.get("/audit/does-not-exist")
    assert resp.status_code == 200
    assert resp.json() == []
