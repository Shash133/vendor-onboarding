"""Dashboard stats API route (Implementation.md §3, §7).

One thin endpoint (no business logic in routes):

- ``GET /dashboard/stats``  the counts and recent activity the dashboard page
  renders as its four ``st.metric`` cards plus the recent-activity list.

Shape (Implementation.md §7 dashboard needs):

    {
      "total": int,           # total submissions
      "pending": int,         # decisions with final_status 'pending'
      "approved": int,        # decisions with final_status 'approved'
      "rejected": int,        # decisions with final_status 'rejected'
      "recent_activity": [ {submission_id, legal_name, status,
                            decision_status, created_at}, ... ]
    }

All counts are computed via parameterized db accessors; the route only maps the
results into the response shape.
"""

from __future__ import annotations

import json

from fastapi import APIRouter

from database import db

router = APIRouter()

# How many recent submissions to surface in the activity feed.
_RECENT_LIMIT = 10


def _parse_json(raw, default):
    """Best-effort JSON parse for the ``form_json`` TEXT column."""
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


@router.get("/dashboard/stats")
def get_dashboard_stats() -> dict:
    """Return submission totals, decision-status counts, and recent activity.

    ``total`` counts all submissions; ``pending``/``approved``/``rejected`` count
    decisions by ``final_status`` (defaulting to 0 when none exist). The recent
    activity feed lists the newest submissions with their vendor name, submission
    status, decision status (if decided) and timestamp.
    """
    by_status = db.count_decisions_by_status()
    recent = [
        {
            "submission_id": row["submission_id"],
            "legal_name": _parse_json(row.get("form_json"), {}).get("legal_name"),
            "status": row["status"],
            "decision_status": row.get("decision_status"),
            "created_at": row["created_at"],
        }
        for row in db.recent_activity(_RECENT_LIMIT)
    ]

    return {
        "total": db.count_submissions(),
        "pending": by_status.get("pending", 0),
        "approved": by_status.get("approved", 0),
        "rejected": by_status.get("rejected", 0),
        "recent_activity": recent,
    }
