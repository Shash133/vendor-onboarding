"""Agent 4 · Risk Assessment (Implementation.md §4, Architecture.md Part 4).

Narrates and weights the fraud signals detected by the deterministic duplicate /
holder checks. Extends the shared :class:`~agents.base.GeminiAgent`: the model is
asked to weigh combinations of signals; on any failure the deterministic
:meth:`fallback` computes the score from the §7 weights table and a templated
rationale.

IMPORTANT (Architecture.md Part 7 / Implementation.md §4):
    The deterministic weight sum (``scoring.compute_scores``) is the SOURCE OF
    TRUTH for ``fraud_risk_score``. This agent only supplies the narrative — the
    score it emits is informational and never overrides the scoring engine.

Input::

    signals: list[dict]   # each {"type": "<FRAUD_WEIGHTS key>", "detail": "..."}

Output schema::

    {"risk_level": "low|medium|high", "fraud_risk_score": 0,
     "signals": [{"type": "string", "severity": "low|medium|high", "detail": "string"}],
     "rationale": "string"}
"""

from __future__ import annotations

import json
import os
from typing import Any

from agents.base import GeminiAgent
from backend.config import (
    FRAUD_PENDING_THRESHOLD,
    FRAUD_REJECT_THRESHOLD,
    FRAUD_SCORE_CAP,
    FRAUD_WEIGHTS,
)

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_DEFAULT_PROMPT = os.path.join(_PROMPTS_DIR, "risk.txt")

RISK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "fraud_risk_score": {"type": "integer"},
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "detail": {"type": "string"},
                },
                "required": ["type", "severity", "detail"],
            },
        },
        "rationale": {"type": "string"},
    },
    "required": ["risk_level", "fraud_risk_score", "signals", "rationale"],
}


def _severity_for_weight(weight: int) -> str:
    """Map a single signal's weight onto a low/medium/high severity band."""
    if weight >= FRAUD_REJECT_THRESHOLD:
        return "high"
    if weight >= FRAUD_PENDING_THRESHOLD:
        return "medium"
    return "low"


def _level_for_score(score: int) -> str:
    """Map an aggregate fraud score onto the decision-gate risk bands (§7)."""
    if score >= FRAUD_REJECT_THRESHOLD:
        return "high"
    if score >= FRAUD_PENDING_THRESHOLD:
        return "medium"
    return "low"


class RiskAssessmentAgent(GeminiAgent):
    """Assess fraud risk from deterministic signals (narrative only)."""

    name = "risk"
    action = "RISK_ASSESSED"
    temperature = 0.0
    response_schema = RISK_SCHEMA

    def __init__(self, client: Any, prompt_path: str | None = None) -> None:
        super().__init__(client, prompt_path or _DEFAULT_PROMPT)

    def _build_parts(self, signals: list) -> list:
        """Combine the prompt with the JSON-encoded signal list."""
        prompt = self._load_prompt()
        return [f"{prompt}\n\nDetected signals (JSON):\n{json.dumps(signals or [])}"]

    def run(self, signals: list, submission_id: Any = None) -> dict:
        """Assess ``signals``; fall back to the deterministic weight sum on failure."""
        return super().run(signals, submission_id=submission_id)

    def fallback(self, signals: list) -> dict:
        """Deterministic backup: weight-sum score (§7) + templated rationale.

        The score mirrors ``scoring._fraud_risk_score`` so the narrative is always
        consistent with the authoritative weight sum used by the decision gate.
        """
        signals = signals or []
        out_signals: list[dict] = []
        total = 0
        for s in signals:
            stype = s.get("type") if isinstance(s, dict) else s
            detail = (s.get("detail") if isinstance(s, dict) else "") or ""
            weight = FRAUD_WEIGHTS.get(stype, 0)
            total += weight
            out_signals.append(
                {"type": stype, "severity": _severity_for_weight(weight), "detail": detail}
            )

        score = int(min(FRAUD_SCORE_CAP, total))
        level = _level_for_score(score)

        if out_signals:
            parts = ", ".join(
                f"{s['type']} (+{FRAUD_WEIGHTS.get(s['type'], 0)})" for s in out_signals
            )
            rationale = (
                f"Computed fraud risk {score}/100 from {len(out_signals)} signal(s): "
                f"{parts}. Risk level assessed as {level}."
            )
        else:
            rationale = "No fraud signals detected; fraud risk is 0 (low)."

        return {
            "risk_level": level,
            "fraud_risk_score": score,
            "signals": out_signals,
            "rationale": rationale,
        }
