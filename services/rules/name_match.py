"""Name-match rules (Architecture.md Part 6, rules 17–19).

    17  NAME_EXACT_MATCH    name  warning  — form vs all docs exact (post-normalise)
    18  NAME_FUZZY_MATCH    name  pending  — fuzzy similarity >= 0.85 across docs
    19  NAME_HARD_MISMATCH  name  reject   — core entity name clearly different

Pure functions ``(ctx) -> RuleResult``; no short-circuit. See Implementation.md §5.
"""

from __future__ import annotations

from models.schemas import RuleResult

from . import get_field, normalize_name, result

CATEGORY = "name"

# Where a legal/holder name appears in each extracted document type.
_NAME_FIELDS = {
    "PAN_CARD": "name",
    "GST_CERTIFICATE": "legal_name",
    "CANCELLED_CHEQUE": "account_holder",
    "BANK_LETTER": "account_holder",
    "VENDOR_REGISTRATION_FORM": "legal_name",
}


def _doc_names(ctx) -> list[tuple[str, str]]:
    """Return (doc_type, name) pairs for every extracted document that has a name."""
    pairs = []
    for doc_type, field in _NAME_FIELDS.items():
        fields = ctx.extracted.get(doc_type)
        if not fields:
            continue
        name = fields.get(field)
        if name:
            pairs.append((doc_type, name))
    return pairs


def _best_name(ctx) -> str | None:
    """Pick a representative document name for the hard-mismatch comparison."""
    names = _doc_names(ctx)
    return names[0][1] if names else None


def check_name_exact_match(ctx) -> RuleResult:
    """Rule 17 — form name exactly matches every doc name after normalisation."""
    form_norm = normalize_name(get_field(ctx.form, "legal_name"))
    names = _doc_names(ctx)
    if not names:
        return result("NAME_EXACT_MATCH", CATEGORY, "warning", True, "No document names to compare.")
    differing = [dt for dt, n in names if normalize_name(n) != form_norm]
    ok = not differing
    return result(
        "NAME_EXACT_MATCH", CATEGORY, "warning", ok,
        "Form name matches all documents exactly." if ok else f"Name not exact in: {differing}.",
    )


def check_name_fuzzy_match(ctx) -> RuleResult:
    """Rule 18 — fuzzy similarity to every doc name is >= 0.85 (pending)."""
    form_name = get_field(ctx.form, "legal_name")
    names = _doc_names(ctx)
    if not names:
        return result("NAME_FUZZY_MATCH", CATEGORY, "pending", True, "No document names to compare.")
    low = []
    for dt, n in names:
        res = ctx.name_match_fn(form_name, n)
        if not res["is_same_entity"]:
            low.append(f"{dt} (sim={res['similarity']})")
    ok = not low
    return result(
        "NAME_FUZZY_MATCH", CATEGORY, "pending", ok,
        "Form name is a fuzzy match across all documents." if ok else f"Low similarity in: {low}.",
    )


def check_name_hard_mismatch(ctx) -> RuleResult:
    """Rule 19 — the core entity name is clearly different (reject).

    A hard mismatch is when names are not the same entity AND similarity < 0.5,
    which distinguishes a genuine different-entity case from a fuzzy variant.
    """
    best = _best_name(ctx)
    if not best:
        # Nothing to compare against → cannot assert a hard mismatch.
        return result("NAME_HARD_MISMATCH", CATEGORY, "reject", True, "No document name to compare.")
    res = ctx.name_match_fn(get_field(ctx.form, "legal_name"), best)
    hard = (not res["is_same_entity"]) and res["similarity"] < 0.5
    return result(
        "NAME_HARD_MISMATCH", CATEGORY, "reject", not hard,
        f"No hard name mismatch ({res['reason']})" if not hard else f"Hard name mismatch: {res['reason']}",
    )


RULES = [
    check_name_exact_match,
    check_name_fuzzy_match,
    check_name_hard_mismatch,
]
