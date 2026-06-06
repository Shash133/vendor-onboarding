"""Agent 2 · Document Extraction (Implementation.md §4, Architecture.md Part 4).

Pulls structured fields from a *classified* document. The ``doc_type`` selects
both the prompt and the target field set, so extraction is targeted. Extends the
shared :class:`~agents.base.GeminiAgent`; on any failure the deterministic
:meth:`fallback` returns all fields null + flags them so a missing field never
silently passes a downstream rule (extraction never blocks the pipeline).

Generic output schema::

    {"fields": {"<name>": {"value": "string|null", "confidence": 0.0, "verbatim": "string"}},
     "unreadable_fields": ["string"]}
"""

from __future__ import annotations

import os
from typing import Any

from google.genai import types

from agents.base import GeminiAgent

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")

# Field set extracted per document type (Implementation.md §4 Agent 2).
FIELD_SETS: dict[str, list[str]] = {
    "PAN_CARD": ["pan", "name"],
    "GST_CERTIFICATE": ["gstin", "legal_name", "address"],
    "CANCELLED_CHEQUE": ["account_number", "ifsc", "account_holder", "bank_name"],
    "BANK_LETTER": ["account_number", "ifsc", "account_holder"],
    "VENDOR_REGISTRATION_FORM": ["legal_name", "pan", "gst", "bank"],
}

# Prompt file used per document type.
PROMPT_FILES: dict[str, str] = {
    "PAN_CARD": "extract_pan.txt",
    "GST_CERTIFICATE": "extract_gst.txt",
    "CANCELLED_CHEQUE": "extract_cheque.txt",
    "BANK_LETTER": "extract_bank_letter.txt",
    "VENDOR_REGISTRATION_FORM": "extract_form.txt",
}


def _schema_for(doc_type: str) -> dict:
    """Build the per-type generic extraction schema for ``response_schema``."""
    fields = FIELD_SETS.get(doc_type, [])
    field_schema = {
        f: {
            "type": "object",
            "properties": {
                "value": {"type": "string", "nullable": True},
                "confidence": {"type": "number"},
                "verbatim": {"type": "string"},
            },
        }
        for f in fields
    }
    return {
        "type": "object",
        "properties": {
            "fields": {"type": "object", "properties": field_schema},
            "unreadable_fields": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["fields", "unreadable_fields"],
    }


class DocumentExtractionAgent(GeminiAgent):
    """Extract type-specific fields from a classified document."""

    name = "extractor"
    action = "EXTRACT_RUN"
    temperature = 0.0

    def __init__(self, client: Any, prompts_dir: str | None = None) -> None:
        # The prompt + schema are selected per-run by doc_type, so the base
        # prompt_path is unused; pass the directory instead.
        super().__init__(client, prompt_path="")
        self.prompts_dir = prompts_dir or _PROMPTS_DIR

    def _prompt_for(self, doc_type: str) -> str:
        """Read the type-specific extraction prompt (defaults to the form prompt)."""
        fname = PROMPT_FILES.get(doc_type, "extract_form.txt")
        with open(os.path.join(self.prompts_dir, fname), "r", encoding="utf-8") as fh:
            return fh.read()

    def _build_parts(self, file_bytes: bytes, mime: str, doc_type: str) -> list:
        """Combine the type-specific prompt with the document bytes."""
        return [
            types.Part.from_bytes(data=file_bytes, mime_type=mime),
            self._prompt_for(doc_type),
        ]

    def run(self, file_bytes: bytes, mime: str, doc_type: str, submission_id: Any = None) -> dict:
        """Extract fields for ``doc_type``; fall back to all-null on failure."""
        # Select the schema for this document type before the base call.
        self.response_schema = _schema_for(doc_type)
        return super().run(file_bytes, mime, doc_type, submission_id=submission_id)

    def fallback(self, file_bytes: bytes, mime: str, doc_type: str) -> dict:
        """Deterministic backup: all fields null + flagged for pending review.

        Extraction never blocks the pipeline — the deterministic rules decide the
        severity of any null/missing field (Implementation.md §4 Agent 2).
        """
        fields = FIELD_SETS.get(doc_type, [])
        return {
            "fields": {f: {"value": None, "confidence": 0.0, "verbatim": ""} for f in fields},
            "unreadable_fields": list(fields),
        }
