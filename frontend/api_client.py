"""Thin HTTP client around the backend API (Implementation.md §3, §7).

The Streamlit frontend talks to FastAPI **only** through this module — no page
imports ``requests`` directly. Each function maps to exactly one backend endpoint
(Implementation.md §3 route table), sends/receives JSON (uploads use
multipart/form-data), and returns the parsed response body.

Configuration: the backend base URL is read from ``BACKEND_URL`` (env), falling
back to :data:`backend.config.BACKEND_URL`, and finally ``http://localhost:8000``.

Error handling: every call funnels through :func:`_request`, which converts
connection failures and non-2xx responses into a single :class:`ApiError` carrying
a friendly message. Pages catch :class:`ApiError` to render a graceful
"backend unavailable" notice instead of crashing the Streamlit runtime.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterator, Optional

import requests

# --- Base URL resolution ------------------------------------------------------


def _default_backend_url() -> str:
    """Resolve the backend base URL.

    Precedence: ``BACKEND_URL`` env var → ``backend.config.BACKEND_URL`` →
    ``http://localhost:8000``. Importing ``backend.config`` is best-effort so the
    frontend still works if it is run from a checkout without the backend package
    importable.
    """
    env = os.getenv("BACKEND_URL")
    if env:
        return env.rstrip("/")
    try:  # best-effort; never let a config import break the UI
        from backend.config import BACKEND_URL as _CONFIG_URL

        if _CONFIG_URL:
            return _CONFIG_URL.rstrip("/")
    except Exception:  # noqa: BLE001 - config is optional for the frontend
        pass
    return "http://localhost:8000"


BACKEND_URL: str = _default_backend_url()

# Per-request timeout (seconds). Generous enough for a synchronous workflow run
# (which fans out to several agents) but bounded so the UI never hangs forever.
DEFAULT_TIMEOUT = 60


class ApiError(Exception):
    """Raised for any backend communication failure (connection or HTTP error).

    ``status_code`` is populated for HTTP errors and ``None`` for transport-level
    failures (e.g. the backend is not running).
    """

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _url(path: str) -> str:
    """Join the configured base URL with an endpoint path."""
    return f"{BACKEND_URL}/{path.lstrip('/')}"


def _request(
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
    files: Any | None = None,
    data: Any | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """Send one HTTP request and return the parsed JSON body.

    Raises :class:`ApiError` with a friendly message on connection problems or a
    non-2xx response. A 2xx response with an empty/invalid body yields ``None``.
    """
    try:
        resp = requests.request(
            method,
            _url(path),
            json=json_body,
            files=files,
            data=data,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError as exc:
        raise ApiError(
            f"Could not reach the backend at {BACKEND_URL}. "
            "Is it running? (uvicorn backend.main:app --reload --port 8000)"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise ApiError(f"The backend at {BACKEND_URL} timed out.") from exc
    except requests.exceptions.RequestException as exc:  # catch-all transport error
        raise ApiError(f"Request to the backend failed: {exc}") from exc

    if not resp.ok:
        detail = _extract_error_detail(resp)
        raise ApiError(
            f"Backend returned {resp.status_code}: {detail}",
            status_code=resp.status_code,
        )

    if not resp.content:
        return None
    try:
        return resp.json()
    except ValueError:
        # Non-JSON 2xx body — return the raw text so callers can still use it.
        return resp.text


def _extract_error_detail(resp: requests.Response) -> str:
    """Pull a human-readable error message out of an error response."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text or resp.reason or "unknown error"
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        return detail if isinstance(detail, str) else json.dumps(detail)
    return json.dumps(body)


# --- Health -------------------------------------------------------------------


def health() -> dict:
    """``GET /health`` — liveness check used to detect an unavailable backend."""
    return _request("GET", "/health", timeout=5)


# --- Submissions --------------------------------------------------------------


def create_submission(form: dict) -> dict:
    """``POST /submissions`` — create a submission from a form dict.

    ``form`` must match the ``SubmissionCreate`` schema (legal_name, pan, gst,
    address, contact_email, contact_phone, vendor_type, bank{...}). Returns
    ``{submission_id, status}``.
    """
    return _request("POST", "/submissions", json_body=form)


def list_submissions() -> list[dict]:
    """``GET /submissions`` — list submissions for the dashboard table."""
    return _request("GET", "/submissions") or []


def get_submission(submission_id: str) -> dict:
    """``GET /submission/{id}`` — full detail (form, docs, results, decision, comms)."""
    return _request("GET", f"/submission/{submission_id}")


# --- Documents ----------------------------------------------------------------


def upload_document(submission_id: str, slot: str, file: Any) -> dict:
    """``POST /documents/upload`` — upload one file for a submission + slot.

    ``file`` may be a Streamlit ``UploadedFile`` (has ``.name``/``.getvalue()``),
    a ``(filename, bytes)`` / ``(filename, bytes, content_type)`` tuple, or raw
    bytes. Returns ``{document_id, slot, file_path}``.
    """
    filename, content, content_type = _coerce_file(file)
    files = {"file": (filename, content, content_type)}
    data = {"submission_id": submission_id, "slot": slot}
    return _request("POST", "/documents/upload", files=files, data=data)


def _coerce_file(file: Any) -> tuple[str, bytes, str]:
    """Normalise the various accepted ``file`` shapes into (name, bytes, mime)."""
    default_type = "application/octet-stream"
    # Streamlit UploadedFile: has .name and .getvalue()
    if hasattr(file, "getvalue") and hasattr(file, "name"):
        content_type = getattr(file, "type", None) or default_type
        return file.name, file.getvalue(), content_type
    # File-like object with .read()/.name
    if hasattr(file, "read") and hasattr(file, "name"):
        return file.name, file.read(), default_type
    # Tuple form
    if isinstance(file, (tuple, list)):
        if len(file) == 3:
            return file[0], file[1], file[2] or default_type
        if len(file) == 2:
            return file[0], file[1], default_type
    # Raw bytes
    if isinstance(file, (bytes, bytearray)):
        return "upload.bin", bytes(file), default_type
    raise ApiError("Unsupported file object passed to upload_document().")


# --- Workflow -----------------------------------------------------------------


def run_workflow(submission_id: str) -> dict:
    """``POST /workflow/run`` — run the full pipeline synchronously.

    Returns ``{submission_id, decision{...}}`` once the pipeline completes.

    Uses a longer timeout than the default: a live run fans out to several
    Gemini calls (classification + per-document extraction + risk / explanation
    / communication), which under the free-tier rate limit can take well over a
    minute. The backend always finishes (agents fall back deterministically on
    failure), so we wait rather than cut the connection early.
    """
    return _request(
        "POST",
        "/workflow/run",
        json_body={"submission_id": submission_id},
        timeout=300,
    )


def stream_workflow(submission_id: str, timeout: int = 120) -> Iterator[dict]:
    """``GET /workflow/stream/{id}`` — yield per-stage SSE events as dicts.

    Each yielded dict has the shape ``{"event": <name>, "data": <parsed json>}``
    where ``event`` is one of ``stage`` / ``complete`` / ``error``. The generator
    finishes when the stream closes. Pages can use this for the live timeline and
    fall back to :func:`get_submission` polling if it raises :class:`ApiError`.
    """
    try:
        resp = requests.get(
            _url(f"/workflow/stream/{submission_id}"),
            stream=True,
            timeout=timeout,
            headers={"Accept": "text/event-stream"},
        )
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"Could not open workflow stream: {exc}") from exc

    if not resp.ok:
        raise ApiError(
            f"Workflow stream returned {resp.status_code}",
            status_code=resp.status_code,
        )

    event_name = "message"
    try:
        for raw_line in resp.iter_lines(decode_unicode=True):
            if raw_line is None:
                continue
            line = raw_line.strip()
            if not line:  # blank line terminates an SSE event block
                event_name = "message"
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                payload = line[len("data:"):].strip()
                try:
                    parsed = json.loads(payload)
                except ValueError:
                    parsed = payload
                yield {"event": event_name, "data": parsed}
    finally:
        resp.close()


# --- Decisions ----------------------------------------------------------------


def get_decision(submission_id: str) -> dict:
    """``GET /decision/{submission_id}`` — decision + scores + explanation + rules."""
    return _request("GET", f"/decision/{submission_id}")


def override_decision(submission_id: str, new_status: str, note: str) -> dict:
    """``POST /decision/{id}/override`` — reviewer override (logged to audit)."""
    return _request(
        "POST",
        f"/decision/{submission_id}/override",
        json_body={"new_status": new_status, "note": note},
    )


# --- Audit & dashboard --------------------------------------------------------


def get_audit(submission_id: str) -> list[dict]:
    """``GET /audit/{submission_id}`` — ordered (oldest-first) audit trail."""
    return _request("GET", f"/audit/{submission_id}") or []


def get_dashboard_stats() -> dict:
    """``GET /dashboard/stats`` — totals + recent activity for the dashboard cards."""
    return _request("GET", "/dashboard/stats")
