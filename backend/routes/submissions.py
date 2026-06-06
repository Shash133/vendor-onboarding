"""Submission API routes (Implementation.md §3).

Three thin endpoints — they validate input, call the db / audit service, and map
rows to response shapes. No business logic lives here (Implementation.md rule:
"All routes are thin: validate → call service → map to response model").

- ``POST /submissions``      create a submission from form JSON.
- ``GET  /submissions``      list submissions for the dashboard table.
- ``GET  /submission/{id}``  full submission detail (form, docs, results,
                             decision, communications).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from database import db
from models.schemas import SubmissionCreate, SubmissionCreateResp
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


@router.post("/submissions", response_model=SubmissionCreateResp)
def create_submission(payload: SubmissionCreate) -> SubmissionCreateResp:
    """Create a ``submissions`` row (status ``received``) and return its id.

    The full form is serialised verbatim into ``form_json``. A ``SUBMISSION_CREATED``
    audit event is written with a short form summary (legal_name, pan, gst).
    """
    submission_id = db.new_id()
    form = payload.model_dump()

    db.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at)"
        " VALUES (?,?,?,?)",
        [submission_id, json.dumps(form), "received", db.utcnow_iso()],
    )

    audit_service.log_event(
        submission_id,
        "system",
        "SUBMISSION_CREATED",
        {
            "legal_name": payload.legal_name,
            "pan": payload.pan,
            "gst": payload.gst,
        },
    )

    return SubmissionCreateResp(submission_id=submission_id, status="received")


@router.get("/submissions")
def list_submissions() -> list[dict]:
    """List submissions for the dashboard table.

    Each entry exposes the fields the dashboard needs: submission_id, the vendor's
    legal_name (read from ``form_json``), submission status, created_at, and the
    decision status / fraud-risk score when a decision exists (NULL otherwise).
    """
    rows = db.list_submissions()
    out: list[dict] = []
    for row in rows:
        form = _parse_json(row.get("form_json"), {})
        out.append(
            {
                "submission_id": row["submission_id"],
                "legal_name": form.get("legal_name"),
                "status": row["status"],
                "created_at": row["created_at"],
                "decision_status": row.get("decision_status"),
                "fraud_risk_score": row.get("fraud_risk_score"),
            }
        )
    return out


@router.get("/submission/{submission_id}")
def get_submission_detail(submission_id: str) -> dict:
    """Return the full detail for one submission, or 404 if it does not exist.

    Composes the parsed form, uploaded documents, validation results, the decision
    (with parsed explanation), and any vendor communications into a single dict.
    """
    submission = db.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    decision = db.get_decision(submission_id)
    if decision is not None:
        decision = {
            **decision,
            "explanation": _parse_json(decision.get("explanation_json"), None),
        }

    communications = [
        {
            **comm,
            "requested_items": _parse_json(comm.get("requested_items_json"), []),
        }
        for comm in db.get_communications(submission_id)
    ]

    return {
        "submission_id": submission_id,
        "status": submission["status"],
        "created_at": submission["created_at"],
        "form": _parse_json(submission.get("form_json"), {}),
        "documents": db.get_documents(submission_id),
        "validation_results": db.get_validation_results(submission_id),
        "decision": decision,
        "communications": communications,
    }
