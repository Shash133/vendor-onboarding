"""Pydantic request/response models + internal dataclasses (Implementation.md §3, §5).

Field names and types are kept exactly as written in Implementation.md §3. Models
use Pydantic v2 conventions (``BaseModel``). ``dict | None`` columns are preserved
as written. The internal :class:`RuleResult` dataclass (§5) is the value object the
validation engine emits.

Types only — no business logic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

# --- Request/response models (Implementation.md §3) ---------------------------


class BankInfo(BaseModel):
    account_number: str
    ifsc: str
    account_holder: str


class SubmissionCreate(BaseModel):
    legal_name: str
    pan: str
    gst: str
    address: str
    contact_email: str
    contact_phone: str
    vendor_type: str  # 'company'|'proprietor'|'partnership'
    bank: BankInfo


class SubmissionCreateResp(BaseModel):
    submission_id: str
    status: str = "received"


class DocumentUploadResp(BaseModel):
    document_id: str
    slot: str
    file_path: str


class RuleResultOut(BaseModel):
    rule_id: str
    category: str
    severity: str
    outcome: str
    reason: str


class DecisionOut(BaseModel):
    final_status: str
    completeness_score: float
    consistency_score: float
    compliance_score: float
    fraud_risk_score: float
    explanation: dict | None
    rule_results: list[RuleResultOut]


class WorkflowRunResp(BaseModel):
    submission_id: str
    decision: DecisionOut


class StageEvent(BaseModel):
    """SSE payload emitted once per workflow stage."""

    stage: str
    status: str  # started|ok|error
    duration_ms: int | None = None
    summary: str | None = None


class AuditEntryOut(BaseModel):
    actor: str
    action: str
    payload: dict | None
    created_at: str


class OverrideRequest(BaseModel):
    new_status: str
    note: str


# --- Internal dataclass (Implementation.md §5) --------------------------------


@dataclass
class RuleResult:
    """Value object returned by each validation rule (pure function output)."""

    rule_id: str
    category: str
    severity: str  # warning|pending|reject
    outcome: str  # pass|warn|fail
    reason: str
