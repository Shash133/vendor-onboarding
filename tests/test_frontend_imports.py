"""Light smoke test for the Streamlit frontend (Task 17).

The Streamlit UI cannot be unit-tested like the API (it needs the Streamlit
runtime), so this test only verifies that:

1. ``frontend.api_client`` imports and exposes a function per backend endpoint
   the pages rely on.
2. Each of the five page modules (and ``frontend.app``) imports cleanly without
   executing any Streamlit runtime code — page rendering is deferred to a
   ``render()`` function, so import is side-effect free.

It never starts the Streamlit server.
"""

import importlib

import pytest

# (module path, required callable attribute) for each page module + app entry.
_PAGE_MODULES = [
    ("frontend.app", "main"),
    ("frontend.pages_.dashboard", "render"),
    ("frontend.pages_.submission_form", "render"),
    ("frontend.pages_.submission_detail", "render"),
    ("frontend.pages_.workflow_timeline", "render"),
    ("frontend.pages_.audit_logs", "render"),
]

# Functions api_client must expose — one per endpoint the pages call.
_API_CLIENT_FUNCS = [
    "create_submission",
    "upload_document",
    "run_workflow",
    "stream_workflow",
    "get_submission",
    "list_submissions",
    "get_decision",
    "override_decision",
    "get_audit",
    "get_dashboard_stats",
    "health",
]


def test_api_client_exposes_endpoint_functions():
    api_client = importlib.import_module("frontend.api_client")
    for name in _API_CLIENT_FUNCS:
        fn = getattr(api_client, name, None)
        assert callable(fn), f"api_client.{name} should be a callable"
    assert hasattr(api_client, "ApiError")
    assert isinstance(api_client.BACKEND_URL, str) and api_client.BACKEND_URL


@pytest.mark.parametrize("module_path, attr", _PAGE_MODULES)
def test_page_modules_import_cleanly(module_path, attr):
    module = importlib.import_module(module_path)
    assert hasattr(module, attr), f"{module_path}.{attr} should exist"
    assert callable(getattr(module, attr))
