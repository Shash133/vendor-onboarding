"""Agent 1 · Document Classification (Implementation.md §4, Architecture.md Part 4).

Labels each uploaded file as one of a fixed document-type set. Extends the shared
:class:`~agents.base.GeminiAgent`: the model is called with a strict JSON
``response_schema``; on any failure the deterministic :meth:`fallback` returns a
safe ``OTHER`` result so an agent can never block the pipeline.

Output schema::

    {"doc_type": "GST_CERTIFICATE|PAN_CARD|CANCELLED_CHEQUE|BANK_LETTER|
                  VENDOR_REGISTRATION_FORM|OTHER",
     "confidence": 0.0, "reason": "string", "legible": true}
"""

from __future__ import annotations

import os
from typing import Any

from google.genai import types

from agents.base import GeminiAgent

# prompts/ lives alongside agents/ under the project root.
_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_DEFAULT_PROMPT = os.path.join(_PROMPTS_DIR, "classify.txt")

DOC_TYPES = [
    "GST_CERTIFICATE",
    "PAN_CARD",
    "CANCELLED_CHEQUE",
    "BANK_LETTER",
    "VENDOR_REGISTRATION_FORM",
    "OTHER",
]

# JSON schema enforced via response_schema (Architecture.md Part 4).
CLASSIFY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "legible": {"type": "boolean"},
    },
    "required": ["doc_type", "confidence", "reason", "legible"],
}


class DocumentClassificationAgent(GeminiAgent):
    """Classify one document into a fixed ``doc_type`` set."""

    name = "classifier"
    action = "CLASSIFY_RUN"
    temperature = 0.0
    response_schema = CLASSIFY_SCHEMA
    # Like extraction, classification uploads document bytes to the model, so it
    # needs more than the default 15s deadline to avoid 504 DEADLINE_EXCEEDED.
    call_timeout_ms = 60_000

    def __init__(self, client: Any, prompt_path: str | None = None) -> None:
        super().__init__(client, prompt_path or _DEFAULT_PROMPT)

    def _build_parts(self, file_bytes: bytes, mime: str, filename: str) -> list:
        """Combine the prompt with the document bytes for the model call."""
        prompt = self._load_prompt()
        return [
            types.Part.from_bytes(data=file_bytes, mime_type=mime),
            f"{prompt}\n\nFilename: {filename}",
        ]

    def run(self, file_bytes: bytes, mime: str, filename: str, submission_id: Any = None) -> dict:
        """Classify ``file_bytes``; fall back to OTHER on any model failure."""
        return super().run(file_bytes, mime, filename, submission_id=submission_id)

    def fallback(self, file_bytes: bytes, mime: str, filename: str) -> dict:
        """Deterministic backup when the model is unavailable (Implementation.md §4)."""
        return {
            "doc_type": "OTHER",
            "confidence": 0.0,
            "reason": "classification unavailable",
            "legible": True,
        }
