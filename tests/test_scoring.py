"""Tests for the scoring engine + decision gate (Task 11, Architecture.md Part 7).

Validates: Requirements 4.1, 4.2.

Covers ``scoring.compute_scores`` (the four sub-scores + §7 fraud-weight sum) and
``scoring.decide`` (the Part 7 gate) across every status path:

    * all-pass rule set                       → approved
    * pending-severity failure (docs missing) → pending
    * reject-severity failure (bad PAN)       → rejected
    * fraud-risk weight-sum thresholds        → pending / rejected

These are pure-function tests: rule lists are built directly as ``RuleResult``
objects so each scenario is isolated from the validation engine.
"""

from __future__ import annotations

from models.schemas import RuleResult
from services import scoring


def rr(rule_id, category, severity, outcome, reason="-") -> RuleResult:
    """Build a RuleResult for a scoring scenario."""
    return RuleResult(rule_id=rule_id, category=category, severity=severity, outcome=outcome, reason=reason)


def all_pass_rules() -> list[RuleResult]:
    """A representative all-pass rule set spanning the scored categories."""
    return [
        rr("FORM_REQUIRED_FIELDS", "completeness", "pending", "pass"),
        rr("MANDATORY_DOCS_PRESENT", "completeness", "pending", "pass"),
        rr("DOC_COUNT_SANITY", "completeness", "pending", "pass"),
        rr("CONTACT_VALID", "completeness", "warning", "pass"),
        rr("PAN_FORMAT", "pan", "reject", "pass"),
        rr("PAN_NAME_MATCH", "pan", "pending", "pass"),
        rr("NAME_FUZZY_MATCH", "name", "pending", "pass"),
        rr("BANK_HOLDER_MATCH", "bank", "pending", "pass"),
        rr("COMPLIANCE_REGISTRATION_PRESENT", "compliance", "reject", "pass"),
    ]


# ============================== APPROVED PATH ================================
def test_all_pass_scores_are_perfect_and_approved():
    rules = all_pass_rules()
    scores = scoring.compute_scores(rules, [])

    assert scores["completeness_score"] == 100.0
    assert scores["consistency_score"] == 100.0
    assert scores["compliance_score"] == 100.0
    assert scores["fraud_risk_score"] == 0.0

    assert scoring.decide(rules, scores) == "approved"


# ============================== PENDING PATHS ================================
def test_pending_when_mandatory_docs_fail():
    rules = all_pass_rules()
    # Flip MANDATORY_DOCS_PRESENT (a pending-severity completeness rule) to fail.
    for i, r in enumerate(rules):
        if r.rule_id == "MANDATORY_DOCS_PRESENT":
            rules[i] = rr("MANDATORY_DOCS_PRESENT", "completeness", "pending", "fail")

    scores = scoring.compute_scores(rules, [])
    # 2 of 3 non-warning completeness rules pass → 66.67, below the 100 floor.
    assert scores["completeness_score"] == round(100.0 * 2 / 3, 2)
    assert scoring.decide(rules, scores) == "pending"


def test_pending_when_fuzzy_name_mismatch_lowers_consistency():
    rules = all_pass_rules()
    for i, r in enumerate(rules):
        if r.rule_id == "PAN_NAME_MATCH":
            rules[i] = rr("PAN_NAME_MATCH", "pan", "pending", "fail")

    scores = scoring.compute_scores(rules, [])
    # One fuzzy mismatch → 100 - 20 = 80 (still meets the consistency floor),
    # but the failing pending-severity rule forces pending.
    assert scores["consistency_score"] == 80.0
    assert scoring.decide(rules, scores) == "pending"


def test_pending_when_fraud_risk_in_medium_band():
    rules = all_pass_rules()
    # DUP_GST(25) + BANK_HOLDER_MATCH(25) = 50 → in [30, 59] medium band.
    signals = [{"type": "DUP_GST"}, {"type": "BANK_HOLDER_MATCH"}]
    scores = scoring.compute_scores(rules, signals)

    assert scores["fraud_risk_score"] == 50.0
    assert scoring.decide(rules, scores) == "pending"


# ============================== REJECTED PATHS ===============================
def test_rejected_when_reject_rule_fails():
    rules = all_pass_rules()
    for i, r in enumerate(rules):
        if r.rule_id == "PAN_FORMAT":
            rules[i] = rr("PAN_FORMAT", "pan", "reject", "fail")

    scores = scoring.compute_scores(rules, [])
    assert scoring.decide(rules, scores) == "rejected"


def test_rejected_when_fraud_risk_at_or_above_reject_threshold():
    rules = all_pass_rules()
    # A single high-weight signal (DUP_BANK_ACCT = 60) hits the reject threshold.
    signals = [{"type": "DUP_BANK_ACCT"}]
    scores = scoring.compute_scores(rules, signals)

    assert scores["fraud_risk_score"] == 60.0
    assert scoring.decide(rules, scores) == "rejected"


def test_rejected_takes_precedence_over_pending():
    # Both a reject fail and a pending fail present → hard reject gate wins.
    rules = all_pass_rules()
    rules.append(rr("PAN_FORMAT_DUP", "pan", "reject", "fail"))
    rules.append(rr("DOC_TYPE_CORRECT", "document", "pending", "fail"))
    scores = scoring.compute_scores(rules, [])
    assert scoring.decide(rules, scores) == "rejected"


# ============================== FRAUD WEIGHT SUM =============================
def test_fraud_weight_sum_matches_weights_table():
    # PAN_REUSE_NEW_BANK(60) + DUP_PAN(30) = 90.
    signals = [{"type": "PAN_REUSE_NEW_BANK"}, {"type": "DUP_PAN"}]
    scores = scoring.compute_scores(all_pass_rules(), signals)
    assert scores["fraud_risk_score"] == 90.0


def test_fraud_score_is_capped_at_100():
    # 60 + 60 + 40 = 160 → capped to 100.
    signals = [
        {"type": "DUP_BANK_ACCT"},
        {"type": "PAN_REUSE_NEW_BANK"},
        {"type": "NAME_HARD_MISMATCH"},
    ]
    scores = scoring.compute_scores(all_pass_rules(), signals)
    assert scores["fraud_risk_score"] == 100.0


def test_fraud_signals_accept_bare_type_strings():
    scores = scoring.compute_scores(all_pass_rules(), ["DUP_PAN", "DUP_GST"])
    assert scores["fraud_risk_score"] == 55.0  # 30 + 25


def test_unknown_fraud_signal_contributes_zero():
    scores = scoring.compute_scores(all_pass_rules(), [{"type": "MYSTERY_SIGNAL"}])
    assert scores["fraud_risk_score"] == 0.0
