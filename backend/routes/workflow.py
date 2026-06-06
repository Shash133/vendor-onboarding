"""Workflow API routes (Implementation.md §3).

Exposes two endpoints:

``POST /workflow/run``
    Run the full onboarding pipeline for a submission synchronously and return
    the final decision.

``GET /workflow/stream/{id}``
    Server-Sent Events (SSE) stream of per-stage events for the live run view
    (Implementation.md §3 / §7 timeline). The synchronous ``workflow_engine.run``
    is executed in a worker thread; its ``emit`` callback pushes a
    :class:`~models.schemas.StageEvent` per stage onto an ``asyncio.Queue``
    (Implementation.md §6), and the :class:`~sse_starlette.EventSourceResponse`
    generator drains that queue to the client until a sentinel signals the run
    has finished. A terminal event carries the final decision status; an error
    event is emitted if the run fails. Either way the stream closes cleanly.

Following the project rule that routes are thin (validate → call service → map to
response), this module only checks the submission exists and bridges the engine's
``emit`` callback to SSE — no business logic lives here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from database import db
from models.schemas import StageEvent, WorkflowRunResp
from services import workflow_engine

logger = logging.getLogger("vendor_onboarding.backend.workflow")

router = APIRouter()

# Sentinel pushed onto the queue (from the worker thread) to tell the SSE
# generator the run has finished and no further stage events will arrive.
_STREAM_DONE = object()


class WorkflowRunRequest(BaseModel):
    """Request body for ``POST /workflow/run``."""

    submission_id: str


@router.post("/workflow/run", response_model=WorkflowRunResp)
def run_workflow(payload: WorkflowRunRequest) -> WorkflowRunResp:
    """Run the pipeline for ``submission_id`` and return the final decision.

    Returns 404 if the submission does not exist; otherwise runs every stage
    synchronously (writing workflow_runs + audit rows) and returns the decision.
    """
    submission = db.get_submission(payload.submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    decision = workflow_engine.run(payload.submission_id)
    return WorkflowRunResp(submission_id=payload.submission_id, decision=decision)


@router.get("/workflow/stream/{submission_id}")
async def stream_workflow(submission_id: str, request: Request) -> EventSourceResponse:
    """Stream per-stage workflow events for ``submission_id`` over SSE.

    Returns 404 if the submission does not exist. Otherwise runs the pipeline in
    a worker thread (``workflow_engine.run`` is synchronous/blocking) while this
    coroutine relays each :class:`StageEvent` to the client as it arrives. A
    final ``complete`` event carries the decision status; a ``error`` event is
    emitted if the run raises. The stream always closes cleanly.
    """
    submission = db.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event: StageEvent) -> None:
        """Engine callback (runs in the worker thread).

        Hands the StageEvent back to the event loop thread-safely so the async
        generator can pick it up and forward it to the client.
        """
        loop.call_soon_threadsafe(queue.put_nowait, ("stage", event.model_dump()))

    def worker() -> None:
        """Run the pipeline to completion, then signal the generator.

        Pushes a terminal ``complete`` payload (with the final decision status
        when available) on success, or an ``error`` payload on failure, followed
        by the ``_STREAM_DONE`` sentinel so the generator can stop.
        """
        try:
            logger.info("SSE: starting workflow run for submission %s", submission_id)
            decision = workflow_engine.run(submission_id, emit=emit)
            final_status = getattr(decision, "final_status", None)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("complete", {"submission_id": submission_id, "final_status": final_status}),
            )
            logger.info(
                "SSE: workflow run for submission %s completed (status=%s)",
                submission_id,
                final_status,
            )
        except Exception as ex:  # noqa: BLE001 - surface to client, then close
            logger.exception("SSE: workflow run for submission %s failed", submission_id)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("error", {"submission_id": submission_id, "error": str(ex)}),
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_DONE)

    thread = threading.Thread(
        target=worker, name=f"workflow-stream-{submission_id}", daemon=True
    )

    async def event_generator():
        """Yield SSE events from the queue until the run signals completion."""
        thread.start()
        try:
            while True:
                item: Any = await queue.get()
                if item is _STREAM_DONE:
                    break
                event_name, payload = item
                yield {"event": event_name, "data": json.dumps(payload)}
        finally:
            # The worker is a daemon thread; give it a brief chance to wind down
            # so its final DB writes/audit rows are flushed before we return.
            if thread.is_alive():
                await loop.run_in_executor(None, thread.join, 5.0)

    return EventSourceResponse(event_generator())
