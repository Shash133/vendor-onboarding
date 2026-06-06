"""Scoring engine + decision gate (Architecture.md Part 7).

This module turns the deterministic ``RuleResult`` list (from the validation
engine) and the collected fraud ``signals`` (from the risk stage) into:

    1. four independent sub-scores (0–100): completeness, consistency, compliance,
       fraud_risk — :func:`compute_scores`; and
    2. a final status of ``"approved" | "pending" | "rejected"`` — :func:`decide`.

Everything here is **pure and deterministic** (no I/O, no clock, no model). The
same inputs always yield the same scores and decision, which is what makes the
decision explainable and replayable (Architecture.md "Determinism first").

Sub-score formulas (Architecture.md Part 7 · "Sub-score formulas")
------------------------------------------------------------------
* **Completeness** — ``100 × (required items present) / (total required)``. The
  doc counts "required_fields_present + required_docs_present". We map "required
  items" to the non-warning completeness-category rules (FORM_REQUIRED_FIELDS,
  MANDATORY_DOCS_PRESENT, DOC_COUNT_SANITY): the fraction that pass. (Simplest
  computation consistent with the doc given we only have rule outcomes here.)
* **Consistency** — start at 100, subtract 20 for each fuzzy name / cross-doc
  mismatch (doc: "fuzzy < 0.85 = −20"). The hard mismatch (NAME_HARD_MISMATCH)
  and the cross-doc reject (BANK_DOC_CONSISTENT) are reject-severity and are
  "handled by the gate" per the doc, so they are NOT double-counted here. The
  penalised rules are therefore the fuzzy name-similarity rules: PAN_NAME_MATCH,
  NAME_FUZZY_MATCH, BANK_HOLDER_MATCH. Floored at 0.
* **Compliance** — ``100 × (compliance_rules_passed / compliance_rules_total)``.
  The compliance category currently has one rule (COMPLIANCE_REGISTRATION_PRESENT).
* **Fraud Risk** — ``min(100, Σ signal weights)`` from the §7 weights table. The
  deterministic weight sum is the source of truth regardless of any LLM narrative.

Decision gate (Architecture.md Part 7 · "Decision logic (order matters)")
-------------------------------------------------------------------------
1. Any reject-severity rule that **failed** → ``rejected`` (hard gate).
2. ``fraud_risk >= FRAUD_REJECT_THRESHOLD`` (60) → ``rejected``.
3. Any pending-severity rule that **failed**, OR ``fraud_risk >= FRAUD_PENDING_THRESHOLD`` (30) → ``pending``.
4. Sub-score floors not met (completeness < 100, consistency < 80, compliance < 100) → ``pending``.
5. Otherwise → ``approved`` (warnings are allowed).

All thresholds and weights come from ``backend/config.py`` (Architecture Part 7).
"""

from __future__ import annotations

from typing import Any

from backend.config import (
    COMPLETENESS_FLOOR,
    COMPLIANCE_FLOOR,
    CONSISTENCY_FLOOR,
    CONSISTENCY_MISMATCH_PENALTY,
    FRAUD_PENDING_THRESHOLD,
    FRAUD_REJECT_THRESHOLD,
    FRAUD_SCORE_CAP,
    FRAUD_WEIGHTS,
)

# Fuzzy name / cross-doc mismatch rules that reduce the consistency score by
# CONSISTENCY_MISMATCH_PENALTY each when they FAIL. These are the pending-severity
# fuzzy-similarity rules; the reject-severity NAME_HARD_MISMATCH and
# BANK_DOC_CONSISTENT are gated separately (Architecture.md Part 7), not scored.
CONSISTENCY_PENALTY_RULES = frozenset(
    {"PAN_NAME_MATCH", "NAME_FUZZY_MATCH", "BANK_HOLDER_MATCH"}
)


def compute_scores(rules: list, signals: list) -> dict:
    """Compute the four sub-scores from rule results + fraud signals (Part 7).

    Parameters
    ----------
    rules:
        The full list of :class:`~models.schemas.RuleResult` from the validation
        engine (every rule, not just failures).
    signals:
        The fraud signals collected by the risk stage. Each entry may be a dict
        (``{"type": "DUP_BANK_ACCT", ...}``) or a bare type string; weights come
        from the §7 ``FRAUD_WEIGHTS`` table.

    Returns
    -------
    dict
        ``{completeness_score, consistency_score, compliance_score, fraud_risk_score}``
        — all floats in 0..100.
    """
    return {
        "completeness_score": _completeness_score(rules),
        "consistency_score": _consistency_score(rules),
        "compliance_score": _compliance_score(rules),
        "fraud_risk_score": _fraud_risk_score(signals),
    }


def decide(rules: list, scores: dict) -> str:
    """Apply the Part 7 decision gate; return ``approved|pending|rejected``.

    Order matters (Architecture.md Part 7): hard reject gate → high fraud →
    pending fails / medium fraud → sub-score floors → approved.
    """
    # 1) HARD GATE — any single reject-severity FAIL ⇒ rejected.
    if any(r.severity == "reject" and r.outcome == "fail" for r in rules):
        return "rejected"

    fraud_risk = scores["fraud_risk_score"]

    # 2) High fraud risk ⇒ rejected.
    if fraud_risk >= FRAUD_REJECT_THRESHOLD:
        return "rejected"

    # 3) Any pending-severity FAIL, or medium fraud ⇒ pending.
    if any(r.severity == "pending" and r.outcome == "fail" for r in rules) or (
        fraud_risk >= FRAUD_PENDING_THRESHOLD
    ):
        return "pending"

    # 4) Sub-score floors (warnings alone never block here).
    if (
        scores["completeness_score"] < COMPLETENESS_FLOOR
        or scores["consistency_score"] < CONSISTENCY_FLOOR
        or scores["compliance_score"] < COMPLIANCE_FLOOR
    ):
        return "pending"

    # 5) Otherwise clean ⇒ approved.
    return "approved"


# --- Sub-score helpers --------------------------------------------------------
def _completeness_score(rules: list) -> float:
    """100 × (passing required completeness checks / total required checks).

    "Required" items are the non-warning completeness-category rules (presence of
    fields and documents). With no such rules present, completeness defaults to
    100 (nothing required is missing).
    """
    presence = [r for r in rules if r.category == "completeness" and r.severity != "warning"]
    if not presence:
        return 100.0
    passed = sum(1 for r in presence if r.outcome == "pass")
    return round(100.0 * passed / len(presence), 2)


def _consistency_score(rules: list) -> float:
    """Start at 100; subtract CONSISTENCY_MISMATCH_PENALTY per fuzzy mismatch."""
    mismatches = sum(
        1 for r in rules if r.rule_id in CONSISTENCY_PENALTY_RULES and r.outcome == "fail"
    )
    return max(0.0, 100.0 - CONSISTENCY_MISMATCH_PENALTY * mismatches)


def _compliance_score(rules: list) -> float:
    """100 × (compliance_rules_passed / compliance_rules_total)."""
    compliance = [r for r in rules if r.category == "compliance"]
    if not compliance:
        return 100.0
    passed = sum(1 for r in compliance if r.outcome == "pass")
    return round(100.0 * passed / len(compliance), 2)


def _fraud_risk_score(signals: list) -> float:
    """min(100, Σ signal weights) from the §7 weights table (source of truth)."""
    total = sum(_signal_weight(s) for s in (signals or []))
    return float(min(FRAUD_SCORE_CAP, total))


def _signal_weight(signal: Any) -> int:
    """Weight for one fraud signal, keyed on its ``type`` via ``FRAUD_WEIGHTS``.

    Accepts a bare type string or a dict. Unknown types contribute 0 unless the
    dict carries an explicit numeric ``weight`` (defensive fallback); the table
    always wins for known types so scoring stays deterministic.
    """
    if isinstance(signal, str):
        return FRAUD_WEIGHTS.get(signal, 0)
    if isinstance(signal, dict):
        stype = signal.get("type")
        if stype in FRAUD_WEIGHTS:
            return FRAUD_WEIGHTS[stype]
        weight = signal.get("weight")
        return int(weight) if isinstance(weight, (int, float)) else 0
    return 0
