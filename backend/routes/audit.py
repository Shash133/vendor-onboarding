"""Audit log API route (Implementation.md §3, §8).

One thin endpoint (no business logic in routes):

- ``GET /audit/{submission_id}``  the ordered (oldest-first) audit trail.

The append-only ``audit_logs`` rows are read through
``services.audit_service.get_audit`` and each row is mapped to an
``AuditEntryOut``-shaped dict: ``actor``, ``action``, ``payload`` (parsed from the
stored ``payload_json`` TEXT column) and ``created_at``.

An unknown submission simply has no rows, so the endpoint returns ``[]`` rather
than 404 — keeping the route simple and the frontend audit page resilient.
"""

from __future__ import annotations

import json

from fastapi import APIRouter

from services import audit_service

router = APIRouter()


def _parse_json(raw, default):
    """Best-effort JSON parse for the ``payload_json`` TEXT column."""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


@router.get("/audit/{submission_id}")
def get_audit(submission_id: str) -> list[dict]:
    """Return a submission's audit log ordered oldest-first.

    Maps each persisted row to the ``AuditEntryOut`` shape. Returns ``[]`` for a
    submission with no events (including unknown submission ids).
    """
    rows = audit_service.get_audit(submission_id)
    return [
        {
            "actor": row["actor"],
            "action": row["action"],
            "payload": _parse_json(row.get("payload_json"), {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]
