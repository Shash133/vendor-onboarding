"""PAN rules (Architecture.md Part 6, rules 5–8).

    5  PAN_FORMAT       pan  reject   — matches [A-Z]{5}[0-9]{4}[A-Z]
    6  PAN_ENTITY_TYPE  pan  warning  — 4th char = entity type consistent w/ declared type
    7  PAN_NAME_MATCH   pan  pending  — form name ≈ PAN card name (fuzzy via name_match_fn)
    8  PAN_DOC_VS_FORM  pan  reject   — PAN on card == PAN on form

Pure functions ``(ctx) -> RuleResult``; no short-circuit. See Implementation.md §5.
"""

from __future__ import annotations

import re

from models.schemas import RuleResult

from . import get_field, result

CATEGORY = "pan"

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

# PAN 4th character encodes the holder type. Map the declared vendor_type to the
# expected 4th-char code (Architecture.md rule 6). 'P' individual/proprietor,
# 'C' company, 'F' firm/partnership, 'H' HUF, 'T' trust, 'A' AOP.
ENTITY_TYPE_CODE = {
    "company": "C",
    "proprietor": "P",
    "individual": "P",
    "partnership": "F",
    "firm": "F",
    "huf": "H",
    "trust": "T",
}


def _form_pan(ctx) -> str:
    return (get_field(ctx.form, "pan") or "").strip().upper()


def check_pan_format(ctx) -> RuleResult:
    """Rule 5 — PAN matches the canonical 10-char pattern (hard reject on fail)."""
    pan = _form_pan(ctx)
    ok = bool(PAN_RE.match(pan))
    return result(
        "PAN_FORMAT", CATEGORY, "reject", ok,
        f"PAN '{pan}' is valid." if ok else f"PAN '{pan}' does not match [A-Z]{{5}}[0-9]{{4}}[A-Z].",
    )


def check_pan_entity_type(ctx) -> RuleResult:
    """Rule 6 — PAN 4th char matches the declared vendor type (informational)."""
    pan = _form_pan(ctx)
    vendor_type = (get_field(ctx.form, "vendor_type") or "").strip().lower()
    expected = ENTITY_TYPE_CODE.get(vendor_type)
    if len(pan) < 4 or expected is None:
        # Can't evaluate (bad PAN length or unknown vendor type) → don't warn.
        return result("PAN_ENTITY_TYPE", CATEGORY, "warning", True, "Entity-type check not applicable.")
    actual = pan[3]
    ok = actual == expected
    return result(
        "PAN_ENTITY_TYPE", CATEGORY, "warning", ok,
        f"PAN entity char '{actual}' matches declared '{vendor_type}'."
        if ok else f"PAN entity char '{actual}' != expected '{expected}' for '{vendor_type}'.",
    )


def check_pan_name_match(ctx) -> RuleResult:
    """Rule 7 — form legal name ≈ name printed on the PAN card (fuzzy)."""
    card_name = (ctx.extracted.get("PAN_CARD") or {}).get("name")
    if not card_name:
        return result("PAN_NAME_MATCH", CATEGORY, "pending", False, "No name extracted from PAN card to compare.")
    res = ctx.name_match_fn(get_field(ctx.form, "legal_name"), card_name)
    return result("PAN_NAME_MATCH", CATEGORY, "pending", res["is_same_entity"], res["reason"])


def check_pan_doc_vs_form(ctx) -> RuleResult:
    """Rule 8 — PAN extracted from the card equals the PAN on the form (reject).

    Present-value contradiction only (Architecture.md Part 8 / Implementation.md
    §9, cases 3 & 4). This hard-reject fires ONLY when an extracted PAN is present
    AND differs from the form PAN — a genuine contradiction a legitimate vendor
    could never produce. When NO PAN was extracted (the wrong document sits in the
    PAN slot, or the scan is illegible) we cannot assert a contradiction, so this
    rule PASSES and the situation is governed by the pending-severity document
    signals (DOC_WRONG_ATTACHED / DOC_LEGIBLE). Treating a missing/unreadable PAN
    as a reject here would force cases 3 & 4 to REJECTED, contradicting the §9
    table which requires them to be PENDING (an honest, fixable incompleteness).
    """
    card_pan = (ctx.extracted.get("PAN_CARD") or {}).get("pan")
    form_pan = _form_pan(ctx)
    if not card_pan:
        # Missing/unreadable extracted PAN → incompleteness, not contradiction.
        return result(
            "PAN_DOC_VS_FORM", CATEGORY, "reject", True,
            "No PAN extracted from card (missing/illegible); deferred to the "
            "pending document checks (DOC_WRONG_ATTACHED / DOC_LEGIBLE).",
        )
    ok = str(card_pan).strip().upper() == form_pan
    return result(
        "PAN_DOC_VS_FORM", CATEGORY, "reject", ok,
        "PAN on card matches form." if ok else f"PAN on card '{card_pan}' != form PAN '{form_pan}'.",
    )


RULES = [
    check_pan_format,
    check_pan_entity_type,
    check_pan_name_match,
    check_pan_doc_vs_form,
]
