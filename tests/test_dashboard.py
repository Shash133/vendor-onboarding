"""Tests for the dashboard stats API route (Task 14, Implementation.md §3, §7).

Exercised against an isolated temp DB using FastAPI's TestClient and the
monkeypatch + importlib.reload pattern (mirroring test_submissions.py).

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
    import backend.routes.dashboard as dashboard
    importlib.reload(dashboard)
    import backend.main as main
    importlib.reload(main)

    db_module.init_db()

    with TestClient(main.app) as test_client:
        yield test_client, db_module


def _form(name, pan="ABCDE1234F", gst="27ABCDE1234F1Z5"):
    return {
        "legal_name": name,
        "pan": pan,
        "gst": gst,
        "address": "1 Industrial Estate, Pune",
        "contact_email": "ops@acme.example",
        "contact_phone": "+919999999999",
        "vendor_type": "company",
        "bank": {
            "account_number": "123456789012",
            "ifsc": "HDFC0001234",
            "account_holder": name,
        },
    }


def _zero_scores(fraud=0.0):
    return {
        "completeness_score": 100.0,
        "consistency_score": 100.0,
        "compliance_score": 100.0,
        "fraud_risk_score": fraud,
    }


def test_dashboard_route_is_registered(client):
    test_client, _ = client
    paths = {route.path for route in test_client.app.routes}
    assert "/dashboard/stats" in paths


def test_dashboard_stats_empty_db(client):
    test_client, _ = client
    resp = test_client.get("/dashboard/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats == {
        "total": 0,
        "pending": 0,
        "approved": 0,
        "rejected": 0,
        "recent_activity": [],
    }


def test_dashboard_stats_counts_and_recent_activity(client):
    test_client, db_module = client

    # Three submissions; seed decisions for two of them.
    ids = []
    for name in ("Alpha Corp", "Bravo Corp", "Charlie Corp"):
        sid = test_client.post("/submissions", json=_form(name)).json()[
            "submission_id"
        ]
        ids.append(sid)

    db_module.insert_decision(ids[0], _zero_scores(), "approved", {"summary": "ok"})
    db_module.insert_decision(ids[1], _zero_scores(fraud=80.0), "rejected", None)
    # ids[2] left undecided (no decision row).

    resp = test_client.get("/dashboard/stats")
    assert resp.status_code == 200
    stats = resp.json()

    assert stats["total"] == 3
    assert stats["approved"] == 1
    assert stats["rejected"] == 1
    assert stats["pending"] == 0

    # Recent activity surfaces all submissions, newest first, with vendor name
    # and decision status (None when undecided).
    activity = stats["recent_activity"]
    assert len(activity) == 3
    by_id = {a["submission_id"]: a for a in activity}
    assert by_id[ids[0]]["legal_name"] == "Alpha Corp"
    assert by_id[ids[0]]["decision_status"] == "approved"
    assert by_id[ids[1]]["decision_status"] == "rejected"
    assert by_id[ids[2]]["decision_status"] is None
    for entry in activity:
        assert set(entry.keys()) == {
            "submission_id",
            "legal_name",
            "status",
            "decision_status",
            "created_at",
        }


def test_dashboard_stats_counts_pending(client):
    test_client, db_module = client

    sid = test_client.post("/submissions", json=_form("Delta Corp")).json()[
        "submission_id"
    ]
    db_module.insert_decision(sid, _zero_scores(fraud=40.0), "pending", None)

    stats = test_client.get("/dashboard/stats").json()
    assert stats["total"] == 1
    assert stats["pending"] == 1
    assert stats["approved"] == 0
    assert stats["rejected"] == 0
