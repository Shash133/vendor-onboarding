"""Tests for the append-only audit service (Task 6, Implementation.md §8)."""

import importlib

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Load db + audit_service against a temporary DB and return both modules."""
    db_file = tmp_path / "test_app.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import services.audit_service as audit_module
    importlib.reload(audit_module)

    db_module.init_db()
    return db_module, audit_module


def _make_submission(db):
    """Insert a submission row so audit_logs' FK is satisfied; return its id."""
    sub_id = db.new_id()
    db.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at)"
        " VALUES (?,?,?,?)",
        [sub_id, "{}", "received", db.utcnow_iso()],
    )
    return sub_id


def test_log_event_inserts_retrievable_row(env):
    db, audit = env
    sub_id = _make_submission(db)

    log_id = audit.log_event(sub_id, "system", "SUBMISSION_CREATED", {"name": "Acme"})
    assert log_id

    rows = audit.get_audit(sub_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["log_id"] == log_id
    assert row["actor"] == "system"
    assert row["action"] == "SUBMISSION_CREATED"
    assert row["payload_json"] == '{"name": "Acme"}'
    assert row["created_at"]


def test_log_event_defaults_payload_to_empty_object(env):
    db, audit = env
    sub_id = _make_submission(db)

    audit.log_event(sub_id, "agent:classifier", "CLASSIFY_OK")
    rows = audit.get_audit(sub_id)
    assert rows[0]["payload_json"] == "{}"


def test_get_audit_orders_oldest_first(env):
    db, audit = env
    sub_id = _make_submission(db)

    audit.log_event(sub_id, "system", "FIRST")
    audit.log_event(sub_id, "system", "SECOND")
    audit.log_event(sub_id, "system", "THIRD")

    actions = [r["action"] for r in audit.get_audit(sub_id)]
    assert actions == ["FIRST", "SECOND", "THIRD"]


def test_get_audit_scoped_to_submission(env):
    db, audit = env
    sub_a = _make_submission(db)
    sub_b = _make_submission(db)

    audit.log_event(sub_a, "system", "A_EVENT")
    audit.log_event(sub_b, "system", "B_EVENT")

    assert [r["action"] for r in audit.get_audit(sub_a)] == ["A_EVENT"]
    assert [r["action"] for r in audit.get_audit(sub_b)] == ["B_EVENT"]


def test_audit_service_is_append_only_no_mutations():
    """Guard the append-only invariant: source contains no UPDATE/DELETE on audit_logs."""
    import services.audit_service as audit
    import inspect

    src = inspect.getsource(audit).upper()
    assert "UPDATE AUDIT_LOGS" not in src
    assert "DELETE FROM AUDIT_LOGS" not in src
