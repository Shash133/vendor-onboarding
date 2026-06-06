"""Streamlit entry point + sidebar navigation (Implementation.md §0, §7).

Run with::

    streamlit run frontend/app.py

This wires the five page modules from :mod:`frontend.pages_` behind a sidebar
radio. It deliberately does **not** use Streamlit's automatic ``pages/`` feature
— the spec layout uses an underscore ``pages_/`` package imported here so we can
manage navigation and the selected submission via ``st.session_state``.

The backend is reached only through :mod:`frontend.api_client`. A backend health
check in the sidebar gives the user a friendly heads-up when it is unavailable.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root (vendor_onboarding/) is importable when Streamlit runs
# this file directly (``streamlit run frontend/app.py`` sets the script dir as
# cwd, not necessarily on sys.path as a package root).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402

from frontend import api_client  # noqa: E402
from frontend.api_client import ApiError  # noqa: E402
from frontend.pages_ import (  # noqa: E402
    audit_logs,
    dashboard,
    submission_detail,
    submission_form,
    workflow_timeline,
)

# Ordered page registry: label → render callable.
PAGES = {
    "Dashboard": dashboard.render,
    "Submission Form": submission_form.render,
    "Submission Detail": submission_detail.render,
    "Workflow Timeline": workflow_timeline.render,
    "Audit Logs": audit_logs.render,
}


def _render_sidebar() -> str:
    """Render the sidebar nav + backend status, returning the selected page."""
    st.sidebar.title("Vendor Onboarding")

    # Keep the radio in sync with programmatic navigation (session_state["page"]).
    page_labels = list(PAGES.keys())
    current = st.session_state.get("page", page_labels[0])
    if current not in page_labels:
        current = page_labels[0]
    selected = st.sidebar.radio(
        "Navigate", page_labels, index=page_labels.index(current)
    )
    st.session_state["page"] = selected

    st.sidebar.divider()
    _render_backend_status()

    selected_id = st.session_state.get("selected_submission_id")
    if selected_id:
        st.sidebar.caption(f"Selected submission:\n`{selected_id}`")

    return selected


def _render_backend_status() -> None:
    """Show a small backend connectivity indicator in the sidebar."""
    try:
        api_client.health()
        st.sidebar.success("Backend: online")
    except ApiError:
        st.sidebar.error("Backend: offline")
        st.sidebar.caption(
            "Start it with:\n`uvicorn backend.main:app --reload --port 8000`"
        )


def main() -> None:
    """Configure the page, render the sidebar, and dispatch to the active page."""
    st.set_page_config(page_title="Vendor Onboarding", page_icon="🤝", layout="wide")

    if "page" not in st.session_state:
        st.session_state["page"] = "Dashboard"

    selected = _render_sidebar()
    PAGES[selected]()


if __name__ == "__main__":
    main()
