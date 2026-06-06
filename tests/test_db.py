"""Tests for the database access layer (Task 2, Implementation.md §2)."""

import importlib
import sqlite3

import pytest

EXPECTED_TABLES = {
    "vendors",
    "submissions",
    "documents",
    "validation_results",
    "decisions",
    "communications",
    "workflow_runs",
    "audit_logs",
}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Load db.py against a temporary DB_PATH and initialise the schema."""
    db_file = tmp_path / "test_app.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    # Reload config + db so the patched DB_PATH is picked up.
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)

    db_module.init_db()
    yield db_module


def test_init_db_creates_all_eight_tables(db):
    rows = db.query(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_init_db_is_idempotent(db):
    # Running init again must not raise (IF NOT EXISTS).
    db.init_db()
    rows = db.query("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_foreign_keys_enabled_on_connection(db):
    conn = db.get_conn()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.row_factory is sqlite3.Row
    finally:
        conn.close()


def test_query_one_returns_none_when_empty(db):
    assert db.get_submission("does-not-exist") is None


def test_insert_and_read_roundtrip(db):
    sub_id = db.new_id()
    db.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at)"
        " VALUES (?,?,?,?)",
        [sub_id, "{}", "received", db.utcnow_iso()],
    )

    sub = db.get_submission(sub_id)
    assert sub is not None
    assert sub["submission_id"] == sub_id
    assert sub["status"] == "received"


def test_decision_and_communication_accessors(db):
    sub_id = db.new_id()
    db.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at)"
        " VALUES (?,?,?,?)",
        [sub_id, "{}", "received", db.utcnow_iso()],
    )

    scores = {
        "completeness_score": 100.0,
        "consistency_score": 100.0,
        "compliance_score": 100.0,
        "fraud_risk_score": 0.0,
    }
    decision_id = db.insert_decision(sub_id, scores, "approved", {"summary": "ok"})
    assert decision_id

    comm = {"subject": "Welcome", "body": "Hi", "requested_items": ["doc"]}
    comm_id = db.insert_communication(decision_id, comm)
    assert comm_id

    db.set_submission_status(sub_id, "decided")
    assert db.get_submission(sub_id)["status"] == "decided"


def test_status_check_constraint_rejects_bad_value(db):
    sub_id = db.new_id()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO submissions(submission_id, form_json, status, created_at)"
            " VALUES (?,?,?,?)",
            [sub_id, "{}", "bogus_status", db.utcnow_iso()],
        )
