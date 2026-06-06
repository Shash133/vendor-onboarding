"""Decision API routes (Implementation.md §3).

Two thin endpoints (no business logic in routes):

- ``GET  /decision/{submission_id}``   decision + scores + explanation + rule_results.
- ``POST /decision/{submission_id}/override``  reviewer override (logged to audit).

The override mutates the ``decisions`` row (allowed — only ``audit_logs`` is
append-only) and records an immutable ``DECISION_OVERRIDDEN`` audit event with
the reviewer actor and old/new status + note.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from database import db
from models.schemas import DecisionOut, OverrideRequest, RuleResultOut
from services import audit_service

router = APIRouter()


def _parse_json(raw, default):
    """Best-effort JSON parse for TEXT columns; fall back to ``default``."""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


@router.get("/decision/{submission_id}", response_model=DecisionOut)
def get_decision(submission_id: str) -> DecisionOut:
    """Return the decision (scores + explanation + rule results), or 404.

    The rule results are read from the persisted ``validation_results`` rows so
    the response mirrors what the workflow produced for this submission.
    """
    decision = db.get_decision(submission_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    rule_rows = db.get_validation_results(submission_id)
    return DecisionOut(
        final_status=decision["final_status"],
        completeness_score=decision["completeness_score"],
        consistency_score=decision["consistency_score"],
        compliance_score=decision["compliance_score"],
        fraud_risk_score=decision["fraud_risk_score"],
        explanation=_parse_json(decision.get("explanation_json"), None),
        rule_results=[
            RuleResultOut(
                rule_id=r["rule_id"],
                category=r["category"],
                severity=r["severity"],
                outcome=r["outcome"],
                reason=r["reason"],
            )
            for r in rule_rows
        ],
    )


@router.post("/decision/{submission_id}/override")
def override_decision(submission_id: str, payload: OverrideRequest) -> dict:
    """Apply a reviewer override and log a ``DECISION_OVERRIDDEN`` audit event.

    Updates the decisions row (``overridden = 1``, ``override_note``,
    ``final_status = new_status``) and appends an immutable audit row capturing
    the reviewer actor and the old/new status + note. Returns 404 if there is no
    decision to override.
    """
    decision = db.get_decision(submission_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision not found")

    old_status = decision["final_status"]
    db.override_decision(submission_id, payload.new_status, payload.note)

    audit_service.log_event(
        submission_id,
        "reviewer",
        "DECISION_OVERRIDDEN",
        {"old_status": old_status, "new_status": payload.new_status, "note": payload.note},
    )

    return {
        "submission_id": submission_id,
        "final_status": payload.new_status,
        "overridden": True,
    }
