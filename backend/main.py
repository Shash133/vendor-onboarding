"""FastAPI application entrypoint (Implementation.md §3).

Responsibilities (kept deliberately thin — no business logic lives here):
- Construct the FastAPI app.
- Enable permissive CORS (local demo, no auth per Implementation.md rule 5).
- Initialise the SQLite schema on startup via ``database.db.init_db()``.
- Expose ``GET /health`` for liveness checks.
- Register the per-feature route modules under ``backend/routes/``.

Route modules (submissions, documents, workflow, decisions, audit, dashboard)
are implemented in later tasks. They are wired in here defensively: each is
imported inside a try/except so the app still boots with only ``/health``
available today. As each module lands it will expose a module-level ``router``
and be picked up automatically — no further changes needed here.
"""

from __future__ import annotations

import importlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import db

logger = logging.getLogger("vendor_onboarding.backend")
logging.basicConfig(level=logging.INFO)

# Route modules to register, in include order. Each module is expected to expose
# a module-level ``router`` (an ``APIRouter``). These are created in later tasks;
# until then the import is guarded so the app boots with just ``/health``.
_ROUTE_MODULES = (
    "submissions",
    "documents",
    "workflow",
    "decisions",
    "audit",
    "dashboard",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the database schema before the app starts serving requests."""
    try:
        db.init_db()
        logger.info("Database initialised (schema ensured).")
    except Exception:  # pragma: no cover - startup failure is logged and re-raised
        logger.exception("Database initialisation failed on startup.")
        raise

    # Opt-in seeding of prior vendors (duplicate/fraud demo cases 9 & 10). Off by
    # default so tests / local runs start clean; enabled on hosted deploys via
    # SEED_ON_STARTUP=1 (see render.yaml) so a fresh ephemeral DB has demo data.
    if os.getenv("SEED_ON_STARTUP", "").strip().lower() in ("1", "true", "yes"):
        try:
            from database.seed import seed

            seeded = seed()
            logger.info("Seeded %d prior vendor(s) on startup.", len(seeded))
        except Exception:  # noqa: BLE001 - seeding is best-effort, never fatal
            logger.warning("Startup seeding failed; continuing without seed data.", exc_info=True)

    yield


app = FastAPI(title="Vendor Onboarding", lifespan=lifespan)

# Permissive CORS: this is a local, single-user demo with no authentication
# (Implementation.md coding rule 5). Allow all origins/methods/headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    """Liveness probe. Returns a simple JSON body once the app is serving."""
    return {"status": "ok"}


@app.get("/health/llm")
def health_llm() -> dict:
    """Live Gemini connectivity check.

    Makes one tiny real call to the configured model and reports whether it
    succeeded, which model answered, and the round-trip latency. Lets you verify
    at a glance that the AI path is actually working (vs. falling back) — e.g.
    open ``/health/llm`` in a browser. Note: this consumes one request from the
    model's quota each time it is called.
    """
    import time

    from agents.base import make_client
    from backend.config import GEMINI_API_KEY, GEMINI_MODEL

    if not GEMINI_API_KEY:
        return {"gemini": "no_key", "model": GEMINI_MODEL,
                "detail": "GEMINI_API_KEY is not set; agents use deterministic fallbacks."}

    t0 = time.monotonic()
    try:
        client = make_client()
        resp = client.models.generate_content(model=GEMINI_MODEL, contents=["ping"])
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "gemini": "ok",
            "model": GEMINI_MODEL,
            "latency_ms": latency_ms,
            "sample": (getattr(resp, "text", "") or "")[:40],
        }
    except Exception as exc:  # noqa: BLE001 - report the real error to the caller
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "gemini": "error",
            "model": GEMINI_MODEL,
            "latency_ms": latency_ms,
            "detail": str(exc)[:300],
        }


def _register_routers(application: FastAPI) -> None:
    """Include any route modules that already exist.

    Each module under ``backend.routes`` is imported defensively. Modules that
    do not exist yet (later tasks) are skipped so the app remains bootable with
    only ``/health``. Modules that exist but fail to import surface a clear log
    message rather than silently disappearing.
    """
    for name in _ROUTE_MODULES:
        module_path = f"backend.routes.{name}"
        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            # Not implemented yet — expected during early build stages.
            logger.info("Route module %s not present yet; skipping. Error: %s", module_path, str(e))
            continue
        except Exception as e:  # pragma: no cover - defensive: surface import errors
            logger.exception("Failed to import route module %s. Error: %s", module_path, str(e))
            continue

        router = getattr(module, "router", None)
        if router is None:
            logger.warning("Route module %s has no 'router'; skipping.", module_path)
            continue

        application.include_router(router)
        logger.info("Registered routes from %s.", module_path)


_register_routers(app)
