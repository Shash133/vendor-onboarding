"""Tests for Agents 4–6 (risk / explanation / communication) — Task 13.

Validates: Requirements 5.1, 5.3, 6.1, 6.2, 6.3.

These exercise the deterministic fallbacks (no API key / forced AgentError) so
they are fully deterministic in CI. The risk fallback score must equal the §7
weight sum, and each agent must return its documented output shape.
"""

import json

import pytest

from agents.communication import VendorCommunicationAgent
from agents.explanation import DecisionExplanationAgent
from agents.risk import RiskAssessmentAgent
from backend.config import FRAUD_WEIGHTS, FRAUD_SCORE_CAP
from models.schemas import RuleResult


# --- Test doubles -------------------------------------------------------------
class _FakeModels:
    """Raises on every call so the agent always uses its fallback."""

    def __init__(self):
        self.calls = 0

    def generate_content(self, *, model, contents, config):
        self.calls += 1
        raise RuntimeError("forced failure → fallback")


class _FailingClient:
    def __init__(self):
        self.models = _FakeModels()


# --- Risk agent ---------------------------------------------------------------
def test_risk_fallback_shape_and_no_signals():
    agent = RiskAssessmentAgent(_FailingClient())
    out = agent.run([])
    assert set(out) == {"risk_level", "fraud_risk_score", "signals", "rationale"}
    assert out["fraud_risk_score"] == 0
    assert out["risk_level"] == "low"
    assert out["signals"] == []
    assert isinstance(out["rationale"], str)


def test_risk_fallback_score_matches_weight_sum():
    signals = [
        {"type": "PAN_REUSE_NEW_BANK", "detail": "reused PAN, new bank"},
        {"type": "BANK_HOLDER_MATCH", "detail": "holder mismatch"},
    ]
    expected = min(
        FRAUD_SCORE_CAP,
        FRAUD_WEIGHTS["PAN_REUSE_NEW_BANK"] + FRAUD_WEIGHTS["BANK_HOLDER_MATCH"],
    )
    agent = RiskAssessmentAgent(_FailingClient())
    out = agent.run(signals)
    assert out["fraud_risk_score"] == expected
    # 60 + 25 = 85 → high band.
    assert out["risk_level"] == "high"
    assert len(out["signals"]) == 2
    assert {s["type"] for s in out["signals"]} == {"PAN_REUSE_NEW_BANK", "BANK_HOLDER_MATCH"}
    for s in out["signals"]:
        assert s["severity"] in {"low", "medium", "high"}


def test_risk_fallback_score_is_capped_at_100():
    signals = [
        {"type": "DUP_BANK_ACCT"},
        {"type": "PAN_REUSE_NEW_BANK"},
    ]  # 60 + 60 = 120 → capped 100
    agent = RiskAssessmentAgent(_FailingClient())
    out = agent.run(signals)
    assert out["fraud_risk_score"] == 100


def test_risk_forced_agent_error_uses_fallback():
    client = _FailingClient()
    agent = RiskAssessmentAgent(client)
    out = agent.run([{"type": "DUP_PAN"}])
    # The model was attempted (and retried) before falling back.
    assert client.models.calls >= 1
    assert out["fraud_risk_score"] == FRAUD_WEIGHTS["DUP_PAN"]


# --- Explanation agent --------------------------------------------------------
def _rule(rule_id, severity, outcome, reason):
    category = "duplicate"
    return RuleResult(rule_id=rule_id, category=category, severity=severity, outcome=outcome, reason=reason)


def test_explanation_fallback_shape_for_clean_decision():
    scores = {
        "completeness_score": 100.0,
        "consistency_score": 100.0,
        "compliance_score": 100.0,
        "fraud_risk_score": 0.0,
    }
    agent = DecisionExplanationAgent(_FailingClient())
    out = agent.run("approved", scores, [])
    assert set(out) == {"summary", "key_drivers", "what_would_change_it"}
    assert "approved" in out["summary"]
    assert out["what_would_change_it"] == []


def test_explanation_fallback_uses_highest_severity_failing_rules():
    scores = {"completeness_score": 100.0, "consistency_score": 80.0, "compliance_score": 100.0, "fraud_risk_score": 60.0}
    rules = [
        _rule("BANK_HOLDER_MATCH", "pending", "fail", "holder mismatch"),
        _rule("PAN_REUSE_NEW_BANK", "reject", "fail", "PAN reused with new bank"),
    ]
    agent = DecisionExplanationAgent(_FailingClient())
    out = agent.run("rejected", scores, rules)
    # The reject-severity rule is the highest severity → it drives the summary.
    assert "PAN_REUSE_NEW_BANK" in out["summary"]
    assert any("PAN_REUSE_NEW_BANK" in d for d in out["key_drivers"])
    assert out["what_would_change_it"]


# --- Communication agent ------------------------------------------------------
def test_communication_pending_lists_missing_items():
    agent = VendorCommunicationAgent(_FailingClient())
    items = ["MANDATORY_DOCS_PRESENT: bank proof missing"]
    out = agent.run("pending", items, "Acme Pvt Ltd", "ops@acme.example")
    assert set(out) == {"subject", "body", "requested_items"}
    assert out["requested_items"] == items
    assert "bank proof missing" in out["body"]


def test_communication_rejected_is_respectful_and_hides_heuristics():
    agent = VendorCommunicationAgent(_FailingClient())
    out = agent.run("rejected", ["PAN_REUSE_NEW_BANK: fraud"], "Acme Pvt Ltd", "ops@acme.example")
    assert out["requested_items"] == []
    body = out["body"].lower()
    # Must not leak internal fraud heuristics / rule names.
    assert "fraud" not in body
    assert "pan_reuse_new_bank" not in body


def test_communication_approved_is_welcome():
    agent = VendorCommunicationAgent(_FailingClient())
    out = agent.run("approved", [], "Acme Pvt Ltd", "ops@acme.example")
    assert out["requested_items"] == []
    assert "approved" in out["body"].lower() or "welcome" in out["subject"].lower()
