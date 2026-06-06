"""Agent 5 · Decision Explanation (Implementation.md §4, Architecture.md Part 4).

Turns the (already-made) decision + rule results into a clear, reviewer-facing
rationale. The agent NEVER makes the decision — the deterministic engine already
did. Extends the shared :class:`~agents.base.GeminiAgent`; on any failure the
deterministic :meth:`fallback` assembles a summary from the reasons of the
highest-severity failing rules.

Input::

    final_status: str
    scores: dict                  # the four sub-scores
    triggered_rules: list         # RuleResult objects (or dicts) — typically non-pass

Output schema::

    {"summary": "string", "key_drivers": ["string"], "what_would_change_it": ["string"]}
"""

from __future__ import annotations

import json
import os
from typing import Any

from agents.base import GeminiAgent

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_DEFAULT_PROMPT = os.path.join(_PROMPTS_DIR, "explanation.txt")

EXPLANATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_drivers": {"type": "array", "items": {"type": "string"}},
        "what_would_change_it": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_drivers", "what_would_change_it"],
}

# Severity ranking for picking the "highest-severity" failing rules in fallback.
_SEVERITY_RANK = {"reject": 3, "pending": 2, "warning": 1}


def _attr(rule: Any, name: str) -> Any:
    """Read ``name`` from a RuleResult dataclass or a plain dict."""
    if isinstance(rule, dict):
        return rule.get(name)
    return getattr(rule, name, None)


def _rule_to_dict(rule: Any) -> dict:
    """Serialise a rule (dataclass or dict) for prompts / audit payloads."""
    return {
        "rule_id": _attr(rule, "rule_id"),
        "category": _attr(rule, "category"),
        "severity": _attr(rule, "severity"),
        "outcome": _attr(rule, "outcome"),
        "reason": _attr(rule, "reason"),
    }


class DecisionExplanationAgent(GeminiAgent):
    """Explain a decision in plain language (prose; never decides)."""

    name = "explanation"
    action = "EXPLANATION_GENERATED"
    temperature = 0.4
    response_schema = EXPLANATION_SCHEMA

    def __init__(self, client: Any, prompt_path: str | None = None) -> None:
        super().__init__(client, prompt_path or _DEFAULT_PROMPT)

    def _build_parts(self, final_status: str, scores: dict, triggered_rules: list) -> list:
        """Combine the prompt with the status, scores, and serialised rules."""
        prompt = self._load_prompt()
        payload = {
            "final_status": final_status,
            "scores": scores,
            "triggered_rules": [_rule_to_dict(r) for r in (triggered_rules or [])],
        }
        return [f"{prompt}\n\nDecision context (JSON):\n{json.dumps(payload)}"]

    def _summarize_request(self, *args: Any, **kwargs: Any) -> dict:
        """Override: serialise RuleResult objects so the audit payload is JSON-safe."""
        final_status = args[0] if len(args) > 0 else None
        scores = args[1] if len(args) > 1 else None
        triggered = args[2] if len(args) > 2 else []
        return {
            "final_status": final_status,
            "scores": scores,
            "triggered_rules": [_rule_to_dict(r) for r in (triggered or [])],
        }

    def run(self, final_status: str, scores: dict, triggered_rules: list, submission_id: Any = None) -> dict:
        """Explain the decision; fall back to a rule-reason summary on failure."""
        return super().run(final_status, scores, triggered_rules, submission_id=submission_id)

    def fallback(self, final_status: str, scores: dict, triggered_rules: list) -> dict:
        """Deterministic backup: summary from the highest-severity failing rules."""
        failing = [r for r in (triggered_rules or []) if _attr(r, "outcome") != "pass"]

        top: list = []
        if failing:
            max_rank = max(_SEVERITY_RANK.get(_attr(r, "severity"), 0) for r in failing)
            top = [r for r in failing if _SEVERITY_RANK.get(_attr(r, "severity"), 0) == max_rank]

        if top:
            drivers = [f"{_attr(r, 'rule_id')}: {_attr(r, 'reason')}" for r in top]
            summary = (
                f"Decision is {final_status}. Primary driver(s): "
                + "; ".join(drivers)
                + "."
            )
            what_would_change = [
                f"Resolve {_attr(r, 'rule_id')} — {_attr(r, 'reason')}" for r in top
            ]
        else:
            drivers = ["All validation checks passed; no fraud signals."]
            summary = (
                f"Decision is {final_status}. All identity, banking, and compliance "
                "checks passed with no fraud signals."
            )
            what_would_change = []

        return {
            "summary": summary,
            "key_drivers": drivers,
            "what_would_change_it": what_would_change,
        }
