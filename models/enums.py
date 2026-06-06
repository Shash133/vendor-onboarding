"""Shared enumerations (Implementation.md §1 `models/enums.py`).

All enums subclass ``str`` so their members serialize cleanly to plain strings in
JSON / SQLite without extra coercion (e.g. ``DocType.PAN_CARD == "PAN_CARD"`` and
``json.dumps`` emits the bare value). Values match the database CHECK constraints
in ``database/schema.sql`` and the document-type set used by the agents.

Types only — no business logic lives here.
"""

from __future__ import annotations

from enum import Enum


class DocType(str, Enum):
    """Document classification labels (Agent 1 output, Architecture.md Part 4)."""

    GST_CERTIFICATE = "GST_CERTIFICATE"
    PAN_CARD = "PAN_CARD"
    CANCELLED_CHEQUE = "CANCELLED_CHEQUE"
    BANK_LETTER = "BANK_LETTER"
    VENDOR_REGISTRATION_FORM = "VENDOR_REGISTRATION_FORM"
    OTHER = "OTHER"


class Severity(str, Enum):
    """Validation-rule severity — worst-case effect on status (schema CHECK)."""

    WARNING = "warning"
    PENDING = "pending"
    REJECT = "reject"


class Outcome(str, Enum):
    """Validation-rule outcome for a single rule (schema CHECK)."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Status(str, Enum):
    """Submission lifecycle status (submissions.status CHECK)."""

    RECEIVED = "received"
    PROCESSING = "processing"
    DECIDED = "decided"


class FinalStatus(str, Enum):
    """Decision outcome (decisions.final_status CHECK)."""

    APPROVED = "approved"
    PENDING = "pending"
    REJECTED = "rejected"


class Stage(str, Enum):
    """Workflow pipeline stages, in execution order (Architecture.md Part 2)."""

    UPLOAD = "upload"
    CLASSIFY = "classify"
    EXTRACT = "extract"
    VALIDATE = "validate"
    CONSISTENCY = "consistency"
    RISK = "risk"
    DECIDE = "decide"
    COMMUNICATE = "communicate"
