"""Validation rule package (Implementation.md §5, Architecture.md Part 6).

This package implements the 28 deterministic validation rules, grouped one module
per category:

    completeness.py  → rules 1–4
    pan.py           → rules 5–8
    gst.py           → rules 9–12
    bank.py          → rules 13–16
    name_match.py    → rules 17–19
    documents.py     → rules 24–28  (incl. the compliance rule 28)
    duplicates.py    → rules 20–23

Each module exposes pure functions ``(ctx) -> RuleResult`` plus a ``RULES`` list
(the ordered functions for that category) so the engine can iterate them. Rules
NEVER short-circuit: every rule runs and contributes a ``RuleResult`` so the audit
log shows the full picture (gating happens later in scoring, Task 11).

Shared helpers (normalisation, nested field access, the default rapidfuzz name
matcher, slot→doc_type mapping, and the outcome factory) live here so every rule
module can import them without duplication.
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from models.schemas import RuleResult

# --- Slot → expected doc_type mapping ----------------------------------------
# A document is uploaded "for" a slot on the intake form. The expected doc_type
# for each slot is used by DOC_TYPE_CORRECT / DOC_WRONG_ATTACHED (rules 24/25).
# The "bank" slot accepts either a cancelled cheque or a bank letter as proof.
SLOT_TO_TYPE: dict[str, Any] = {
    "pan": "PAN_CARD",
    "gst": "GST_CERTIFICATE",
    "bank": {"CANCELLED_CHEQUE", "BANK_LETTER"},
    "registration": "VENDOR_REGISTRATION_FORM",
}


def slot_to_type(slot: str | None) -> Any:
    """Return the expected doc_type (or set of types) for a form ``slot``.

    Returns ``None`` when the slot is unknown (such slots are not checked).
    """
    if not slot:
        return None
    return SLOT_TO_TYPE.get(slot.strip().lower())


# --- Field access / normalisation --------------------------------------------
def get_field(form: dict, path: str) -> Any:
    """Read a possibly-nested form field by dotted ``path`` (e.g. ``bank.ifsc``).

    Returns ``None`` if any segment is missing or a non-dict is encountered.
    """
    cur: Any = form
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str | None) -> str:
    """Normalise a legal name for exact comparison.

    Lowercases, strips punctuation, and collapses whitespace so that
    "Acme Technologies Pvt. Ltd." and "acme technologies pvt ltd" compare equal.
    """
    if not name:
        return ""
    lowered = _NON_ALNUM.sub(" ", str(name).lower())
    return " ".join(lowered.split())


# --- Legal-suffix canonicalisation (fuzzy matching only) ---------------------
# Architecture.md Part 4 (Agent 3) requires the consistency check to "account for
# legal-suffix variants (Pvt Ltd / Private Limited), abbreviations". When no live
# model is available the deterministic fallback must still honour that design
# intent, otherwise Implementation.md §9 case 8 — a LEGITIMATE suffix/abbreviation
# variant ("Acme Tech Pvt Ltd" vs "Acme Technologies Private Limited") that the §9
# table requires to be APPROVED — could never pass the 0.85 fuzzy threshold (raw
# token_sort_ratio ≈ 0.68). Expanding the common Indian legal-form abbreviations
# before the ratio lifts that genuine variant over the threshold while leaving
# truly different names well below it. Used by BOTH the default matcher here and
# the Agent 3 fallback so the two stay byte-for-byte consistent.
_LEGAL_SUFFIX_MAP = {
    "pvt": "private",
    "ltd": "limited",
    "co": "company",
    "corp": "corporation",
    "inc": "incorporated",
    "intl": "international",
}


def canonicalize_legal_name(name: str | None) -> str:
    """Normalise ``name`` and expand common legal-suffix abbreviations.

    Builds on :func:`normalize_name` (NOT a replacement for it — exact-match rules
    keep using the stricter ``normalize_name``), then maps abbreviated legal forms
    to their full words so "pvt ltd" ≡ "private limited" for fuzzy comparison.
    """
    norm = normalize_name(name)
    if not norm:
        return ""
    return " ".join(_LEGAL_SUFFIX_MAP.get(tok, tok) for tok in norm.split())


def fuzzy_name_similarity(name_a: str | None, name_b: str | None) -> float:
    """Return the rapidfuzz token_sort_ratio (0..1) over canonicalised names.

    Single source of truth for fuzzy name similarity so the default matcher and
    Agent 3's deterministic fallback always agree. Returns 0.0 if either name is
    empty after canonicalisation.
    """
    a = canonicalize_legal_name(name_a)
    b = canonicalize_legal_name(name_b)
    if not a or not b:
        return 0.0
    return round(fuzz.token_sort_ratio(a, b) / 100.0, 4)


# --- Default (deterministic) name matcher ------------------------------------
# IMPORTANT (Task 12 integration seam): rules that need fuzzy name matching call
# ``ctx.name_match_fn(name_a, name_b)``. The default below is a deterministic
# rapidfuzz implementation (token_sort_ratio/100 over canonicalised names,
# same-entity at >= 0.85). Agent 3 (the ConsistencyCheckingAgent, Task 12) can be
# injected in its place WITHOUT changing any rule code — it only needs to honour
# the same dict contract:
#     {"is_same_entity": bool, "similarity": float, "reason": str}
def default_name_match(name_a: str | None, name_b: str | None) -> dict:
    """Deterministic fuzzy name comparison using rapidfuzz token_sort_ratio.

    Returns ``similarity`` in 0..1 and ``is_same_entity`` when similarity >= 0.85.
    Legal-suffix abbreviations are expanded first (see :func:`canonicalize_legal_name`).
    """
    a = (name_a or "").strip()
    b = (name_b or "").strip()
    if not a or not b:
        return {
            "is_same_entity": False,
            "similarity": 0.0,
            "reason": "One or both names are empty; cannot compare.",
        }
    similarity = fuzzy_name_similarity(name_a, name_b)
    is_same = similarity >= 0.85
    verdict = "same entity" if is_same else "different entity"
    return {
        "is_same_entity": is_same,
        "similarity": similarity,
        "reason": f"rapidfuzz token_sort_ratio={similarity:.2f} → {verdict}.",
    }


# --- RuleResult factory -------------------------------------------------------
def result(rule_id: str, category: str, severity: str, passed: bool, reason: str) -> RuleResult:
    """Build a :class:`RuleResult`, mapping ``passed`` → the right outcome.

    Outcome convention (consistent with the scoring gate in Task 11):
      - ``pass`` when the check holds.
      - ``warn`` when a *warning*-severity check does not hold (informational).
      - ``fail`` when a *pending* or *reject* severity check does not hold.
    """
    if passed:
        outcome = "pass"
    else:
        outcome = "warn" if severity == "warning" else "fail"
    return RuleResult(rule_id=rule_id, category=category, severity=severity, outcome=outcome, reason=reason)
