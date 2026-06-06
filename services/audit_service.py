"""Audit logging service (Implementation.md §8).

Writes append-only rows into the ``audit_logs`` table. Every workflow stage,
agent run, decision, and reviewer override is recorded here so the audit trail
shows the full picture of how a submission was processed.

APPEND-ONLY INVARIANT: this module only ever INSERTs into ``audit_logs``. It
MUST NEVER issue an UPDATE or DELETE against that table. The audit log is the
immutable record of system behaviour; mutating it would break traceability.
"""

from __future__ import annotations

import json

from database import db


def log_event(
    submission_id: str,
    actor: str,
    action: str,
    payload: dict | None = None,
) -> str:
    """Append one row to ``audit_logs`` and return the generated log_id.

    Parameters mirror the canonical event table in Implementation.md §8:
    ``actor`` is one of ``'system' | 'agent:<name>' | 'reviewer'``, ``action``
    is a canonical event string, and ``payload`` is JSON-serialised (defaults to
    ``{}`` when omitted). All values are passed as parameters; nothing is
    string-interpolated into the SQL.
    """
    log_id = db.new_id()
    db.execute(
        "INSERT INTO audit_logs("
        "log_id, submission_id, actor, action, payload_json, created_at"
        ") VALUES (?,?,?,?,?,?)",
        [
            log_id,
            submission_id,
            actor,
            action,
            json.dumps(payload or {}),
            db.utcnow_iso(),
        ],
    )
    return log_id


def get_audit(submission_id: str) -> list[dict]:
    """Return a submission's audit rows ordered oldest-first (read-only).

    Thin reader for the audit route (Implementation.md §3 ``GET /audit/{id}``).
    """
    return db.query(
        "SELECT * FROM audit_logs WHERE submission_id = ? ORDER BY created_at ASC",
        [submission_id],
    )
