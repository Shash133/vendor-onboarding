"""Audit logs page (Implementation.md §7, §8).

Components: a chronological audit table with actor/action filters and an export
button (JSON / CSV). Reads the trail via ``GET /audit/{id}`` through
:mod:`frontend.api_client`.

All rendering lives in :func:`render`; importing the module has no side effects.
"""

from __future__ import annotations

import csv
import io
import json

import streamlit as st

from frontend import api_client
from frontend.api_client import ApiError


def render() -> None:
    """Render the audit log for the selected submission."""
    st.title("📜 Audit Logs")

    submission_id = st.session_state.get("selected_submission_id")
    if not submission_id:
        st.info("Select a submission from the dashboard first.")
        return

    st.caption(f"Submission `{submission_id}`")

    try:
        entries = api_client.get_audit(submission_id)
    except ApiError as exc:
        st.error(exc.message)
        return

    if not entries:
        st.info("No audit entries for this submission yet.")
        return

    # --- Filters --------------------------------------------------------------
    actors = sorted({e.get("actor", "") for e in entries})
    actions = sorted({e.get("action", "") for e in entries})
    col_actor, col_action = st.columns(2)
    actor_filter = col_actor.selectbox("Actor", options=["All"] + actors)
    action_filter = col_action.selectbox("Action", options=["All"] + actions)

    filtered = [
        e
        for e in entries
        if (actor_filter == "All" or e.get("actor") == actor_filter)
        and (action_filter == "All" or e.get("action") == action_filter)
    ]

    # --- Export buttons -------------------------------------------------------
    col_json, col_csv = st.columns(2)
    col_json.download_button(
        "⬇️ Export JSON",
        data=json.dumps(filtered, indent=2),
        file_name=f"audit_{submission_id}.json",
        mime="application/json",
    )
    col_csv.download_button(
        "⬇️ Export CSV",
        data=_to_csv(filtered),
        file_name=f"audit_{submission_id}.csv",
        mime="text/csv",
    )

    # --- Chronological table --------------------------------------------------
    st.subheader("Trail")
    header = st.columns([3, 2, 3, 4])
    header[0].markdown("**Time**")
    header[1].markdown("**Actor**")
    header[2].markdown("**Action**")
    header[3].markdown("**Payload**")
    for e in filtered:
        cols = st.columns([3, 2, 3, 4])
        cols[0].write(e.get("created_at", "—"))
        cols[1].write(e.get("actor", "—"))
        cols[2].write(e.get("action", "—"))
        payload = e.get("payload")
        if payload:
            cols[3].json(payload, expanded=False)
        else:
            cols[3].write("—")


def _to_csv(entries: list[dict]) -> str:
    """Serialise audit entries to CSV text (payload rendered as JSON string)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["created_at", "actor", "action", "payload"])
    for e in entries:
        writer.writerow(
            [
                e.get("created_at", ""),
                e.get("actor", ""),
                e.get("action", ""),
                json.dumps(e.get("payload") or {}),
            ]
        )
    return buffer.getvalue()
