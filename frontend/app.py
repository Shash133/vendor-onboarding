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

import os
import sys
from pathlib import Path

# Ensure the project root (vendor_onboarding/) is importable when Streamlit runs
# this file directly (``streamlit run frontend/app.py`` sets the script dir as
# cwd, not necessarily on sys.path as a package root).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st  # noqa: E402


def _bridge_secrets_to_env() -> None:
    """Copy Streamlit Cloud secrets into ``os.environ`` before backend imports.

    On Streamlit Community Cloud, configuration is provided via ``st.secrets``
    (set in the app's dashboard). ``backend.config`` reads plain environment
    variables, so we mirror a known set of keys into ``os.environ`` here — before
    anything imports ``backend.config`` — without overriding values already set
    in the real environment (local dev / .env still win in that case).

    Accessing ``st.secrets`` raises if no secrets are configured (e.g. local
    runs), so the whole thing is best-effort.
    """
    keys = ("GEMINI_API_KEY", "GEMINI_MODEL", "DB_PATH", "UPLOAD_DIR", "BACKEND_URL")
    try:
        for key in keys:
            if key in st.secrets and not os.getenv(key):
                os.environ[key] = str(st.secrets[key])
    except Exception:  # noqa: BLE001 - no secrets configured is fine
        pass


_bridge_secrets_to_env()

# Start the in-process FastAPI backend (single-host / Streamlit Cloud deploy).
# Imported and invoked AFTER secrets are bridged so the backend sees the config.
from frontend import server as _backend_server  # noqa: E402


@st.cache_resource(show_spinner="Starting backend…")
def _boot_backend() -> bool:
    """Start the in-process backend exactly once per Streamlit server process."""
    ok = _backend_server.ensure_backend()
    if ok:
        _backend_server.seed_if_empty()
    return ok


_boot_backend()

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
