"""Agent 3 · Consistency Checking (fuzzy name matching only).

Implementation.md §4, Architecture.md Part 4. Judges whether two name strings
refer to the same legal entity — the one comparison too fuzzy for regex (exact
comparisons like PAN==PAN stay deterministic). Extends the shared
:class:`~agents.base.GeminiAgent`; on any failure the deterministic
:meth:`fallback` uses ``rapidfuzz.fuzz.token_sort_ratio`` with a 0.85 threshold,
so consistency never depends solely on the LLM.

Output schema::

    {"is_same_entity": true, "similarity": 0.0,
     "normalized_a": "string", "normalized_b": "string", "reason": "string"}

INTEGRATION SEAM (Task 12): the validation rules call ``ctx.name_match_fn(a, b)``
and consume a dict with keys ``is_same_entity`` / ``similarity`` / ``reason``
(see ``services/rules/__init__.py`` ``default_name_match``). :meth:`as_name_match_fn`
adapts this agent's ``run()`` output to exactly that contract so the agent can be
injected in place of the deterministic matcher without changing any rule code.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from agents.base import GeminiAgent
from backend.config import NAME_FUZZY_THRESHOLD
from services.rules import fuzzy_name_similarity, normalize_name

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_DEFAULT_PROMPT = os.path.join(_PROMPTS_DIR, "consistency.txt")

CONSISTENCY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "is_same_entity": {"type": "boolean"},
        "similarity": {"type": "number"},
        "normalized_a": {"type": "string"},
        "normalized_b": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["is_same_entity", "similarity", "normalized_a", "normalized_b", "reason"],
}


class ConsistencyCheckingAgent(GeminiAgent):
    """Decide whether two names denote the same legal entity (fuzzy)."""

    name = "consistency"
    action = "CONSISTENCY_CHECK"
    temperature = 0.0
    response_schema = CONSISTENCY_SCHEMA

    def __init__(self, client: Any, prompt_path: str | None = None) -> None:
        super().__init__(client, prompt_path or _DEFAULT_PROMPT)

    def _build_parts(self, name_a: str, name_b: str) -> list:
        """Combine the prompt with the two names to compare."""
        prompt = self._load_prompt()
        return [f"{prompt}\n\nName A: {name_a}\nName B: {name_b}"]

    def run(self, name_a: str, name_b: str, submission_id: Any = None) -> dict:
        """Compare two names; fall back to rapidfuzz on any model failure."""
        return super().run(name_a, name_b, submission_id=submission_id)

    def fallback(self, name_a: str, name_b: str) -> dict:
        """Deterministic backup: rapidfuzz token_sort_ratio, same-entity >= 0.85.

        Matches the contract consumed by the validation rules (keys
        ``is_same_entity`` / ``similarity`` / ``reason``) and additionally returns
        the normalized forms required by the agent's own output schema. The
        similarity is computed by the shared :func:`services.rules.fuzzy_name_similarity`
        (legal-suffix-aware, Architecture.md Part 4) so this fallback stays
        byte-for-byte consistent with the default matcher the rules use.
        """
        a = (name_a or "").strip()
        b = (name_b or "").strip()
        norm_a = normalize_name(name_a)
        norm_b = normalize_name(name_b)
        if not a or not b:
            return {
                "is_same_entity": False,
                "similarity": 0.0,
                "normalized_a": norm_a,
                "normalized_b": norm_b,
                "reason": "One or both names are empty; cannot compare.",
            }
        similarity = fuzzy_name_similarity(name_a, name_b)
        is_same = similarity >= NAME_FUZZY_THRESHOLD
        verdict = "same entity" if is_same else "different entity"
        return {
            "is_same_entity": is_same,
            "similarity": similarity,
            "normalized_a": norm_a,
            "normalized_b": norm_b,
            "reason": f"rapidfuzz token_sort_ratio={similarity:.2f} → {verdict}.",
        }

    def as_name_match_fn(self, submission_id: Any = None) -> Callable[[Any, Any], dict]:
        """Return a ``(name_a, name_b) -> dict`` adapter for ``ctx.name_match_fn``.

        The returned callable invokes :meth:`run` (LLM with deterministic
        rapidfuzz fallback) and projects the result onto the rules' contract:
        ``{"is_same_entity": bool, "similarity": float, "reason": str}``.
        """

        def _fn(name_a: Any, name_b: Any) -> dict:
            res = self.run(name_a, name_b, submission_id=submission_id)
            return {
                "is_same_entity": res["is_same_entity"],
                "similarity": res["similarity"],
                "reason": res["reason"],
            }

        return _fn
