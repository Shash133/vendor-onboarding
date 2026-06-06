"""Tests for shared models and enums (Task 3, Implementation.md §3 + §5).

These are type-only smoke tests: each enum has the expected str-backed members and
each Pydantic model instantiates with the field names/types from §3.
"""

from models.enums import (
    DocType,
    FinalStatus,
    Outcome,
    Severity,
    Stage,
    Status,
)
from models.schemas import (
    AuditEntryOut,
    BankInfo,
    DecisionOut,
    DocumentUploadResp,
    OverrideRequest,
    RuleResult,
    RuleResultOut,
    StageEvent,
    SubmissionCreate,
    SubmissionCreateResp,
    WorkflowRunResp,
)


def test_enums_are_str_backed_and_serialize_cleanly():
    assert DocType.PAN_CARD == "PAN_CARD"
    assert Severity.REJECT == "reject"
    assert Outcome.FAIL == "fail"
    assert Status.RECEIVED == "received"
    assert FinalStatus.APPROVED == "approved"
    assert Stage.VALIDATE == "validate"
    # str-backed members JSON-encode to their bare value.
    import json

    assert json.dumps(DocType.GST_CERTIFICATE) == '"GST_CERTIFICATE"'


def test_doctype_has_exactly_the_six_labels():
    assert {d.value for d in DocType} == {
        "GST_CERTIFICATE",
        "PAN_CARD",
        "CANCELLED_CHEQUE",
        "BANK_LETTER",
        "VENDOR_REGISTRATION_FORM",
        "OTHER",
    }


def test_stage_order_matches_pipeline():
    assert [s.value for s in Stage] == [
        "upload",
        "classify",
        "extract",
        "validate",
        "consistency",
        "risk",
        "decide",
        "communicate",
    ]


def test_submission_create_roundtrips_nested_bank():
    sub = SubmissionCreate(
        legal_name="Acme Technologies Private Limited",
        pan="ABCDE1234F",
        gst="27ABCDE1234F1Z5",
        address="1 Industrial Estate, Pune",
        contact_email="ops@acme.example",
        contact_phone="+919999999999",
        vendor_type="company",
        bank=BankInfo(
            account_number="123456789012",
            ifsc="HDFC0001234",
            account_holder="Acme Technologies Private Limited",
        ),
    )
    assert sub.bank.ifsc == "HDFC0001234"


def test_submission_create_resp_defaults_to_received():
    assert SubmissionCreateResp(submission_id="s1").status == "received"


def test_decision_out_allows_none_explanation_and_holds_rule_results():
    decision = DecisionOut(
        final_status="approved",
        completeness_score=100.0,
        consistency_score=100.0,
        compliance_score=100.0,
        fraud_risk_score=0.0,
        explanation=None,
        rule_results=[
            RuleResultOut(
                rule_id="PAN_FORMAT",
                category="pan",
                severity="reject",
                outcome="pass",
                reason="ok",
            )
        ],
    )
    assert decision.explanation is None
    assert decision.rule_results[0].rule_id == "PAN_FORMAT"


def test_workflow_run_resp_wraps_decision():
    decision = DecisionOut(
        final_status="pending",
        completeness_score=80.0,
        consistency_score=100.0,
        compliance_score=100.0,
        fraud_risk_score=0.0,
        explanation={"summary": "missing doc"},
        rule_results=[],
    )
    resp = WorkflowRunResp(submission_id="s1", decision=decision)
    assert resp.decision.final_status == "pending"


def test_stage_event_optional_fields_default_none():
    ev = StageEvent(stage="classify", status="started")
    assert ev.duration_ms is None and ev.summary is None


def test_document_upload_resp_and_audit_and_override():
    up = DocumentUploadResp(document_id="d1", slot="pan", file_path="uploads/s1/d1.pdf")
    assert up.slot == "pan"

    entry = AuditEntryOut(
        actor="system", action="SUBMISSION_CREATED", payload=None, created_at="2024-01-01T00:00:00Z"
    )
    assert entry.payload is None

    ovr = OverrideRequest(new_status="approved", note="manual review ok")
    assert ovr.new_status == "approved"


def test_rule_result_dataclass_fields():
    rr = RuleResult(
        rule_id="MANDATORY_DOCS_PRESENT",
        category="completeness",
        severity="pending",
        outcome="fail",
        reason="Missing bank proof",
    )
    assert rr.rule_id == "MANDATORY_DOCS_PRESENT"
    assert rr.outcome == "fail"
