"""Tests for the FastAPI skeleton (Task 5, Implementation.md §3).

Covers Requirement 2.1 (workflow/app boots) and Requirement 10.3 (every endpoint
has a test) for the ``GET /health`` liveness endpoint. Also asserts the app boots
and runs ``init_db()`` on startup without error via the TestClient lifespan.
"""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient against a temporary DB so startup init_db() is isolated."""
    db_file = tmp_path / "health_app.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    # Reload config + db + main so the patched DB_PATH is used on startup.
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import backend.main as main_module
    importlib.reload(main_module)

    # Entering the context manager triggers the lifespan startup (init_db()).
    with TestClient(main_module.app) as test_client:
        yield test_client, db_file


def test_health_returns_ok(client):
    test_client, _ = client
    resp = test_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_startup_initialises_database(client):
    # Lifespan startup should have created the SQLite file via init_db().
    _, db_file = client
    assert db_file.exists()


def test_app_is_importable_and_boots():
    # Importing the module and constructing the app must not raise.
    import backend.main as main_module
    importlib.reload(main_module)
    assert main_module.app is not None
    # Health route is registered.
    paths = {route.path for route in main_module.app.routes}
    assert "/health" in paths
