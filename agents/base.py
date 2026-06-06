"""Gemini agent base class (Implementation.md §4).

This module provides the shared contract for all six onboarding agents
(classification, extraction, consistency, risk, explanation, communication).
Concrete agents live in their own modules and are implemented in later tasks;
this file only defines the generic machinery they build on.

Shared agent contract (Architecture.md Part 4):
- Model: Gemini 2.5 Flash (``GEMINI_MODEL`` from ``backend.config``).
- Structured output enforced via ``response_mime_type="application/json"`` plus
  a JSON ``response_schema``. The raw response is always parsed; free text is
  never trusted.
- Failure handling: 15s timeout -> one retry -> if still failing, raise
  ``AgentError``. The public ``run()`` catches ``AgentError`` and returns a
  deterministic ``fallback(...)`` so an agent can never block the pipeline.
- Every ``run()`` writes the raw request/response to the audit log.

NOTE: ``services/audit_service.py`` does not exist yet (it arrives in Task 6).
It is imported lazily inside ``run()`` and guarded with ``try/except`` so a
missing module never crashes an agent.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from backend.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

# Request timeout for a single Gemini call. ``HttpOptions.timeout`` is expressed
# in milliseconds, so 15s == 15_000ms (Architecture.md Part 4: "timeout (15s)").
_CALL_TIMEOUT_MS = 15_000


class AgentError(Exception):
    """Raised when a Gemini call fails (after retry) or returns invalid JSON.

    ``run()`` catches this and falls back to a deterministic backup so the
    pipeline is never blocked by an agent failure.
    """


def make_client(api_key: str | None = None) -> genai.Client:
    """Construct a google-genai client from ``GEMINI_API_KEY``.

    Kept intentionally minimal: callers may inject their own client instead
    (the agents accept a client in ``__init__``). The key falls back to
    ``backend.config.GEMINI_API_KEY`` when not supplied explicitly.
    """
    key = api_key or GEMINI_API_KEY
    if not key:
        raise AgentError(
            "GEMINI_API_KEY is not set; cannot create a Gemini client. "
            "Set it in the environment / .env before running agents."
        )
    return genai.Client(api_key=key)


class GeminiAgent:
    """Base class for all Gemini-backed agents.

    Subclasses set ``name`` and ``response_schema`` (and optionally
    ``temperature`` and ``action``), implement ``_build_parts`` to assemble the
    model input, and override ``fallback`` with a deterministic backup.
    """

    #: Short agent name, used in audit actor strings (e.g. "classifier").
    name: str = "agent"
    #: Sampling temperature. 0.0 for extraction/classification/matching;
    #: prose agents (explanation/communication) override with ~0.4.
    temperature: float = 0.0
    #: JSON schema enforced via ``response_schema``. Subclasses must set this.
    response_schema: dict = {}
    #: Audit action label. Defaults to ``"<NAME>_RUN"`` when not set.
    action: str | None = None

    def __init__(self, client: Any, prompt_path: str) -> None:
        """Store the (injected) genai client and the prompt file path."""
        self.client = client
        self.prompt_path = prompt_path
        self._prompt_cache: str | None = None

    # --- Prompt loading -------------------------------------------------------
    def _load_prompt(self) -> str:
        """Read and cache the prompt text from ``prompt_path``."""
        if self._prompt_cache is None:
            with open(self.prompt_path, "r", encoding="utf-8") as fh:
                self._prompt_cache = fh.read()
        return self._prompt_cache

    # --- Model call -----------------------------------------------------------
    def _call(self, parts: list, *, retries: int = 1) -> dict:
        """Call Gemini with JSON-schema-constrained output and parse the result.

        Uses ``response_mime_type='application/json'`` + ``response_schema`` and
        a 15s timeout. On any exception or invalid JSON the call is retried
        ``retries`` more times; if it still fails an ``AgentError`` is raised.
        """
        config = types.GenerateContentConfig(
            temperature=self.temperature,
            response_mime_type="application/json",
            response_schema=self.response_schema or None,
            http_options=types.HttpOptions(timeout=_CALL_TIMEOUT_MS),
        )

        last_error: Exception | None = None
        attempts = retries + 1  # initial attempt + ``retries`` retries
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=parts,
                    config=config,
                )
                text = getattr(response, "text", None)
                if not text:
                    raise ValueError("model returned an empty response")
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("model response was not a JSON object")
                return parsed
            except Exception as exc:  # noqa: BLE001 - retry on any failure
                last_error = exc
                logger.warning(
                    "[%s] Gemini call failed (attempt %d/%d): %s",
                    self.name, attempt, attempts, exc,
                )

        raise AgentError(
            f"[{self.name}] Gemini call failed after {attempts} attempt(s): {last_error}"
        ) from last_error

    # --- Public entry ---------------------------------------------------------
    def _build_parts(self, *args: Any, **kwargs: Any) -> list:
        """Assemble the model input parts for a single run.

        Generic base: concrete agents override this to combine the loaded prompt
        with their specific inputs (text, file bytes, etc.).
        """
        raise NotImplementedError("Subclasses must implement _build_parts().")

    def run(self, *args: Any, **kwargs: Any) -> dict:
        """Public entry point: build parts, call the model, fall back on error.

        ``submission_id`` may be passed as a keyword for audit correlation; it is
        not forwarded to ``_build_parts``/``fallback``. On ``AgentError`` the
        deterministic ``fallback(...)`` result is returned instead of raising.
        Every invocation writes an audit event (request + response).
        """
        submission_id = kwargs.pop("submission_id", None)

        parts = self._build_parts(*args, **kwargs)
        request_summary = self._summarize_request(*args, **kwargs)

        try:
            response = self._call(parts)
        except AgentError as exc:
            logger.warning("[%s] falling back after AgentError: %s", self.name, exc)
            response = self.fallback(*args, **kwargs)

        self._write_audit(submission_id, request_summary, response)
        return response

    def fallback(self, *args: Any, **kwargs: Any) -> dict:
        """Deterministic backup used when the model call fails.

        The base implementation raises ``NotImplementedError``; every concrete
        agent overrides this with a safe, model-free result (later tasks).
        """
        raise NotImplementedError("Subclasses must implement fallback().")

    # --- Helpers --------------------------------------------------------------
    def _summarize_request(self, *args: Any, **kwargs: Any) -> dict:
        """Build a small, JSON-serialisable summary of the request for audit.

        Raw bytes are reported by length only so the audit payload stays
        readable and serialisable.
        """
        def describe(value: Any) -> Any:
            if isinstance(value, (bytes, bytearray)):
                return f"<{len(value)} bytes>"
            return value

        return {
            "args": [describe(a) for a in args],
            "kwargs": {k: describe(v) for k, v in kwargs.items()},
        }

    def _write_audit(self, submission_id: Any, request: Any, response: Any) -> None:
        """Write a best-effort audit event for this run.

        ``services.audit_service`` arrives in Task 6, so it is imported lazily
        and any failure (missing module included) is swallowed with a log line.
        """
        try:
            # Lazy import: audit_service is implemented in Task 6.
            from services import audit_service
        except Exception:  # noqa: BLE001 - module not present yet (Task 6)
            logger.debug(
                "[%s] audit_service unavailable (arrives in Task 6); skipping audit",
                self.name,
            )
            return

        try:
            audit_service.log_event(
                submission_id,
                actor=f"agent:{self.name}",
                action=self.action or f"{self.name.upper()}_RUN",
                payload={"request": request, "response": response},
            )
        except Exception as exc:  # noqa: BLE001 - never let auditing break a run
            logger.warning("[%s] audit logging failed: %s", self.name, exc)
