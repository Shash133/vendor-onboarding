"""Workflow timeline page (Implementation.md §7).

Components: a vertical stepper with one row per stage, updating live. Expanding a
stage shows its raw status / duration / summary (agent I/O surfaced through the
stage events and the persisted submission detail).

Live updates: it first tries the SSE stream ``GET /workflow/stream/{id}`` via
:func:`frontend.api_client.stream_workflow`. If the stream is unavailable it
falls back to polling ``GET /submission/{id}`` (the page reruns roughly every
500ms via a short sleep) until a decision is present.

All rendering lives in :func:`render`; importing the module has no side effects.
"""

from __future__ import annotations

import time

import streamlit as st

from frontend import api_client
from frontend.api_client import ApiError

# Canonical stage order (Implementation.md §6). "communicate" only runs for
# pending/rejected decisions, so it may not appear for an approved submission.
_STAGE_ORDER = [
    "upload",
    "classify",
    "extract",
    "validate",
    "consistency",
    "risk",
    "decide",
    "communicate",
]

_STATUS_ICON = {"started": "🔄", "ok": "✅", "error": "❌"}

# Poll cadence for the fallback path (Implementation.md §7: ~500ms).
_POLL_INTERVAL_S = 0.5


def render() -> None:
    """Render the live workflow timeline for the selected submission."""
    st.title("⏱️ Workflow Timeline")

    submission_id = st.session_state.get("selected_submission_id")
    if not submission_id:
        st.info("Select or create a submission first.")
        return

    st.caption(f"Submission `{submission_id}`")

    mode = st.radio(
        "Live update mode",
        options=["Stream (SSE)", "Poll (500ms)"],
        horizontal=True,
        index=0,
    )

    if mode == "Stream (SSE)":
        _render_stream(submission_id)
    else:
        _render_poll(submission_id)

    _render_open_detail()


def _render_stream(submission_id: str) -> None:
    """Consume the SSE stream and render the stepper; fall back to polling."""
    placeholder = st.empty()
    stages: dict[str, dict] = {}
    final_status = None
    try:
        for event in api_client.stream_workflow(submission_id):
            name = event.get("event")
            data = event.get("data") or {}
            if name == "stage" and isinstance(data, dict):
                stage = data.get("stage")
                if stage:
                    stages[stage] = data
                    with placeholder.container():
                        _render_stepper(stages)
            elif name == "complete" and isinstance(data, dict):
                final_status = data.get("final_status")
            elif name == "error":
                st.error(f"Workflow error: {data}")
        with placeholder.container():
            _render_stepper(stages)
        if final_status:
            st.success(f"Workflow finished — decision: {final_status}")
        elif not stages:
            st.info("No live events received; showing the latest saved state.")
            _render_from_detail(submission_id)
    except ApiError:
        st.warning("Live stream unavailable — falling back to polling.")
        _render_poll(submission_id)


def _render_poll(submission_id: str) -> None:
    """Polling fallback: render saved state and rerun until a decision exists."""
    decided = _render_from_detail(submission_id)
    if not decided:
        st.caption("Waiting for the pipeline to finish…")
        time.sleep(_POLL_INTERVAL_S)
        st.rerun()
    else:
        st.success("Workflow finished — decision available.")


def _render_from_detail(submission_id: str) -> bool:
    """Render a stepper from persisted workflow runs. Returns True if decided."""
    try:
        detail = api_client.get_submission(submission_id)
    except ApiError as exc:
        st.error(exc.message)
        return True  # stop polling on error

    runs = detail.get("workflow_runs") or []
    if runs:
        stages = {r.get("stage"): r for r in runs if r.get("stage")}
        _render_stepper(stages)
    else:
        # The detail endpoint may not surface workflow_runs; show stage names
        # with an unknown status so the user still sees the pipeline shape.
        _render_stepper({})

    decision = detail.get("decision")
    return bool(decision) or detail.get("status") == "decided"


def _render_stepper(stages: dict[str, dict]) -> None:
    """Render the vertical stepper, one row per stage with expandable detail."""
    for stage in _STAGE_ORDER:
        info = stages.get(stage)
        status = (info or {}).get("status")
        icon = _STATUS_ICON.get(status, "⏳")
        duration = (info or {}).get("duration_ms")
        suffix = f" · {duration} ms" if duration is not None else ""
        with st.expander(f"{icon} {stage}{suffix}", expanded=False):
            if not info:
                st.caption("Not started / no data.")
                continue
            st.write(f"**Status:** {status or '—'}")
            if duration is not None:
                st.write(f"**Duration:** {duration} ms")
            summary = info.get("summary") or info.get("output_summary")
            if summary:
                st.write("**Output summary:**")
                st.code(str(summary))


def _render_open_detail() -> None:
    """Quick link to the detail page for the current submission."""
    st.divider()
    if st.button("Open submission detail"):
        st.session_state["page"] = "Submission Detail"
        st.rerun()
