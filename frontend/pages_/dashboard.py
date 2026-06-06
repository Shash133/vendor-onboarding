"""Dashboard page (Implementation.md §7).

Components: 4 ``st.metric`` cards (Total/Pending/Approved/Rejected), a status
filter, a submissions table, and a recent-activity list. Clicking a row selects
that submission and routes to the detail page.

API calls: ``GET /dashboard/stats`` (cards + recent activity) and
``GET /submissions`` (table). All access goes through :mod:`frontend.api_client`.

The page renders entirely inside :func:`render` so importing this module never
touches the Streamlit runtime (keeps the smoke test cheap and side-effect free).
"""

from __future__ import annotations

import streamlit as st

from frontend import api_client
from frontend.api_client import ApiError


def _go_to_detail(submission_id: str) -> None:
    """Select a submission and navigate to the detail page on next rerun."""
    st.session_state["selected_submission_id"] = submission_id
    st.session_state["page"] = "Submission Detail"


def render() -> None:
    """Render the dashboard page."""
    st.title("📊 Vendor Onboarding Dashboard")

    try:
        stats = api_client.get_dashboard_stats()
        submissions = api_client.list_submissions()
    except ApiError as exc:
        st.error(exc.message)
        st.info("Start the backend, then refresh this page.")
        return

    # --- Metric cards ---------------------------------------------------------
    col_total, col_pending, col_approved, col_rejected = st.columns(4)
    col_total.metric("Total", stats.get("total", 0))
    col_pending.metric("Pending", stats.get("pending", 0))
    col_approved.metric("Approved", stats.get("approved", 0))
    col_rejected.metric("Rejected", stats.get("rejected", 0))

    st.divider()

    # --- Filters --------------------------------------------------------------
    filter_col, search_col = st.columns([1, 2])
    with filter_col:
        status_filter = st.selectbox(
            "Filter by decision status",
            options=["All", "pending", "approved", "rejected", "undecided"],
            index=0,
        )
    with search_col:
        search = st.text_input("Search by name or PAN", value="").strip().lower()

    rows = _filter_rows(submissions, status_filter, search)

    # --- Submissions table ----------------------------------------------------
    st.subheader("Submissions")
    if not rows:
        st.info("No submissions match the current filter.")
    else:
        header = st.columns([3, 2, 2, 3, 1])
        header[0].markdown("**Vendor**")
        header[1].markdown("**Status**")
        header[2].markdown("**Decision**")
        header[3].markdown("**Created**")
        header[4].markdown("**Open**")
        for row in rows:
            cols = st.columns([3, 2, 2, 3, 1])
            cols[0].write(row.get("legal_name") or "—")
            cols[1].write(row.get("status") or "—")
            decision_status = row.get("decision_status")
            risk = row.get("fraud_risk_score")
            decision_label = decision_status or "—"
            if risk is not None:
                decision_label = f"{decision_label} (risk {risk:g})"
            cols[2].write(decision_label)
            cols[3].write(row.get("created_at") or "—")
            cols[4].button(
                "View",
                key=f"open_{row['submission_id']}",
                on_click=_go_to_detail,
                args=(row["submission_id"],),
            )

    st.divider()

    # --- Recent activity ------------------------------------------------------
    st.subheader("Recent activity")
    recent = stats.get("recent_activity", [])
    if not recent:
        st.caption("No recent activity yet.")
    for item in recent:
        name = item.get("legal_name") or item.get("submission_id")
        decision_status = item.get("decision_status") or "no decision"
        st.write(
            f"• **{name}** — {item.get('status')} / {decision_status} "
            f"· {item.get('created_at')}"
        )


def _filter_rows(
    submissions: list[dict], status_filter: str, search: str
) -> list[dict]:
    """Apply the decision-status filter and the name/PAN search to the rows."""
    rows = submissions
    if status_filter == "undecided":
        rows = [r for r in rows if not r.get("decision_status")]
    elif status_filter != "All":
        rows = [r for r in rows if r.get("decision_status") == status_filter]

    if search:
        def _matches(row: dict) -> bool:
            name = (row.get("legal_name") or "").lower()
            pan = (row.get("pan") or "").lower()
            return search in name or search in pan

        rows = [r for r in rows if _matches(r)]
    return rows
