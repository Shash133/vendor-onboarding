"""Bank rules (Architecture.md Part 6, rules 13–16).

    13  BANK_IFSC_FORMAT     bank  reject   — IFSC matches [A-Z]{4}0[A-Z0-9]{6}
    14  BANK_ACCT_FORMAT     bank  pending  — account number numeric, plausible length
    15  BANK_HOLDER_MATCH    bank  pending  — account holder ≈ vendor legal name (fuzzy)
    16  BANK_DOC_CONSISTENT  bank  reject   — acct/IFSC on cheque == bank letter == form

Pure functions ``(ctx) -> RuleResult``; no short-circuit. See Implementation.md §5.
"""

from __future__ import annotations

import re

from models.schemas import RuleResult

from . import get_field, result

CATEGORY = "bank"

IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
# Indian bank account numbers are typically 9–18 digits.
_ACCT_RE = re.compile(r"^\d{9,18}$")

# Documents that carry bank account/IFSC evidence to cross-check against the form.
_BANK_DOC_TYPES = ("CANCELLED_CHEQUE", "BANK_LETTER")


def check_ifsc_format(ctx) -> RuleResult:
    """Rule 13 — IFSC matches the canonical pattern (reject on fail)."""
    ifsc = (get_field(ctx.form, "bank.ifsc") or "").strip().upper()
    ok = bool(IFSC_RE.match(ifsc))
    return result(
        "BANK_IFSC_FORMAT", CATEGORY, "reject", ok,
        f"IFSC '{ifsc}' is valid." if ok else f"IFSC '{ifsc}' does not match [A-Z]{{4}}0[A-Z0-9]{{6}}.",
    )


def check_bank_acct_format(ctx) -> RuleResult:
    """Rule 14 — account number is numeric and of a plausible length (pending)."""
    acct = str(get_field(ctx.form, "bank.account_number") or "").strip()
    ok = bool(_ACCT_RE.match(acct))
    return result(
        "BANK_ACCT_FORMAT", CATEGORY, "pending", ok,
        "Account number format is plausible." if ok else f"Account number '{acct}' is not 9–18 digits.",
    )


def check_bank_holder_match(ctx) -> RuleResult:
    """Rule 15 — account holder name ≈ vendor legal name (fuzzy, pending)."""
    holder = get_field(ctx.form, "bank.account_holder")
    if not holder:
        return result("BANK_HOLDER_MATCH", CATEGORY, "pending", False, "No account holder name provided to compare.")
    res = ctx.name_match_fn(get_field(ctx.form, "legal_name"), holder)
    return result("BANK_HOLDER_MATCH", CATEGORY, "pending", res["is_same_entity"], res["reason"])


def check_bank_doc_consistent(ctx) -> RuleResult:
    """Rule 16 — acct/IFSC on bank docs equal the form values (reject on mismatch).

    Only documents actually present are checked; a missing bank document cannot
    contradict the form, so absence does not fail this rule (that is covered by
    the mandatory-docs completeness rule).
    """
    form_acct = str(get_field(ctx.form, "bank.account_number") or "").strip()
    form_ifsc = (get_field(ctx.form, "bank.ifsc") or "").strip().upper()

    mismatches = []
    for doc_type in _BANK_DOC_TYPES:
        fields = ctx.extracted.get(doc_type)
        if not fields:
            continue
        d_acct = str(fields.get("account_number") or "").strip()
        d_ifsc = str(fields.get("ifsc") or "").strip().upper()
        if d_acct and d_acct != form_acct:
            mismatches.append(f"{doc_type} account {d_acct} != form {form_acct}")
        if d_ifsc and d_ifsc != form_ifsc:
            mismatches.append(f"{doc_type} IFSC {d_ifsc} != form {form_ifsc}")

    ok = not mismatches
    return result(
        "BANK_DOC_CONSISTENT", CATEGORY, "reject", ok,
        "Bank document details match the form." if ok else "; ".join(mismatches) + ".",
    )


RULES = [
    check_ifsc_format,
    check_bank_acct_format,
    check_bank_holder_match,
    check_bank_doc_consistent,
]
