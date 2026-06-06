"""In-process FastAPI launcher for single-host (Streamlit Cloud) deployment.

Streamlit Community Cloud runs exactly one process — the Streamlit script — and
cannot expose a second public port for the FastAPI backend. To deploy both tiers
from one GitHub-connected Streamlit app, we start the FastAPI app with uvicorn in
a **background daemon thread** bound to ``127.0.0.1:<port>`` and let the frontend
reach it over loopback (the same ``BACKEND_URL`` the local dev setup uses).

This module is import-safe and idempotent: :func:`ensure_backend` starts the
server at most once per process (guarded by a lock + flag) and blocks briefly
until ``/health`` responds, so the first page render already has a live backend.

Locally (``make run-backend`` + ``make run-frontend``) this is not used — the
frontend simply talks to the separately-running uvicorn. It only kicks in when
``ensure_backend()`` is called explicitly (see ``frontend/app.py``).
"""

from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("vendor_onboarding.frontend.server")

# Loopback host/port the in-process backend binds to. The frontend's api_client
# defaults to http://localhost:8000, so these must agree (or set BACKEND_URL).
_HOST = "127.0.0.1"
_PORT = int(os.getenv("BACKEND_PORT", "8000"))

_started = False
_lock = threading.Lock()


def _run_server() -> None:
    """Run uvicorn in this thread (no signal handlers — not the main thread)."""
    import uvicorn

    from backend.main import app

    config = uvicorn.Config(
        app,
        host=_HOST,
        port=_PORT,
        log_level="warning",
        # The default lifespan runs db.init_db() on startup.
        lifespan="on",
    )
    server = uvicorn.Server(config)
    # Signal handlers can only be installed on the main thread; disable them so
    # running the server from a worker thread does not raise.
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]
    try:
        server.run()
    except OSError as exc:
        # e.g. WinError 10048 / EADDRINUSE — another backend already owns the
        # port. That is fine: ensure_backend() will detect the existing server
        # via the health check and reuse it. Log and let this thread exit.
        logger.warning("Backend thread could not bind %s:%d (%s); "
                       "assuming an existing backend is running.", _HOST, _PORT, exc)


def _is_backend_up(timeout: float = 1.5) -> bool:
    """Return True if a backend is already serving /health on the target port."""
    import requests

    try:
        return requests.get(f"http://{_HOST}:{_PORT}/health", timeout=timeout).ok
    except requests.exceptions.RequestException:
        return False


def _wait_for_health(timeout: float) -> bool:
    """Poll ``/health`` until it responds OK or ``timeout`` seconds elapse."""
    import requests

    deadline = time.monotonic() + timeout
    url = f"http://{_HOST}:{_PORT}/health"
    while time.monotonic() < deadline:
        try:
            resp = requests.get(url, timeout=2)
            if resp.ok:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.25)
    return False


def ensure_backend(timeout: float = 30.0) -> bool:
    """Start the in-process FastAPI backend once and wait until it is healthy.

    Returns ``True`` if the backend responds to ``/health`` within ``timeout``.
    Safe to call on every Streamlit rerun: the server is launched at most once.
    """
    global _started
    with _lock:
        if not _started:
            # If a backend is already listening (e.g. a separately-run uvicorn,
            # or a leftover process on the port), reuse it instead of trying to
            # bind again — binding twice raises WinError 10048 / EADDRINUSE.
            if _is_backend_up():
                _started = True
                logger.info("Reusing existing backend on %s:%d", _HOST, _PORT)
                return True

            thread = threading.Thread(
                target=_run_server, name="fastapi-backend", daemon=True
            )
            thread.start()
            _started = True
            logger.info("Started in-process FastAPI backend on %s:%d", _HOST, _PORT)

    return _wait_for_health(timeout)


def seed_if_empty() -> None:
    """Best-effort seed of prior vendors for the duplicate/fraud demo cases.

    Idempotent (fixed vendor ids). Any failure is logged and swallowed so a seed
    problem never blocks the UI from loading.
    """
    try:
        from database.seed import seed

        seed()
    except Exception as exc:  # noqa: BLE001 - seeding is best-effort
        logger.warning("Vendor seeding skipped: %s", exc)
