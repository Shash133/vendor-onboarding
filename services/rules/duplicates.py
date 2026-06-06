"""Duplicate / fraud-lookup rules (Architecture.md Part 6, rules 20–23).

    20  DUP_PAN              duplicate  pending  — PAN already on another vendor
    21  DUP_GST              duplicate  pending  — GST already on another vendor
    22  DUP_BANK_ACCT        duplicate  reject   — bank account shared with another vendor
    23  PAN_REUSE_NEW_BANK   duplicate  reject   — same PAN, different bank details

These rules query the ``vendors`` table via ``ctx.db.query_one`` (parameterized),
always excluding the current vendor. Run last because they need form + extracted
fields. See Implementation.md §5.
"""

from __future__ import annotations

from models.schemas import RuleResult

from . import get_field, result

CATEGORY = "duplicate"


def _exclude_id(ctx) -> str:
    """Sentinel vendor id to exclude self-matches (empty when no vendor yet)."""
    return ctx.vendor_id or ""


def check_dup_pan(ctx) -> RuleResult:
    """Rule 20 — PAN already exists on a different vendor (pending)."""
    pan = (get_field(ctx.form, "pan") or "").strip().upper()
    row = ctx.db.query_one(
        "SELECT vendor_id FROM vendors WHERE UPPER(pan) = ? AND vendor_id != ?",
        [pan, _exclude_id(ctx)],
    )
    ok = row is None
    return result(
        "DUP_PAN", CATEGORY, "pending", ok,
        "PAN is not used by another vendor." if ok else f"PAN already registered to vendor {row['vendor_id']}.",
    )


def check_dup_gst(ctx) -> RuleResult:
    """Rule 21 — GST already exists on a different vendor (pending)."""
    gst = (get_field(ctx.form, "gst") or "").strip().upper()
    row = ctx.db.query_one(
        "SELECT vendor_id FROM vendors WHERE UPPER(gst) = ? AND vendor_id != ?",
        [gst, _exclude_id(ctx)],
    )
    ok = row is None
    return result(
        "DUP_GST", CATEGORY, "pending", ok,
        "GST is not used by another vendor." if ok else f"GST already registered to vendor {row['vendor_id']}.",
    )


def check_dup_bank_acct(ctx) -> RuleResult:
    """Rule 22 — bank account shared with another vendor (reject)."""
    acct = str(get_field(ctx.form, "bank.account_number") or "").strip()
    row = ctx.db.query_one(
        "SELECT vendor_id FROM vendors WHERE bank_account = ? AND vendor_id != ?",
        [acct, _exclude_id(ctx)],
    )
    ok = row is None
    return result(
        "DUP_BANK_ACCT", CATEGORY, "reject", ok,
        "Bank account is not shared with another vendor."
        if ok else f"Bank account already linked to vendor {row['vendor_id']}.",
    )


def check_pan_reuse_new_bank(ctx) -> RuleResult:
    """Rule 23 — same PAN as an existing vendor but with different bank details (reject)."""
    pan = (get_field(ctx.form, "pan") or "").strip().upper()
    acct = str(get_field(ctx.form, "bank.account_number") or "").strip()
    row = ctx.db.query_one(
        "SELECT vendor_id, bank_account FROM vendors WHERE UPPER(pan) = ? AND vendor_id != ?",
        [pan, _exclude_id(ctx)],
    )
    fail = bool(row) and (row.get("bank_account") or "") != acct
    return result(
        "PAN_REUSE_NEW_BANK", CATEGORY, "reject", not fail,
        "No reused PAN with changed bank details."
        if not fail else f"PAN reused from vendor {row['vendor_id']} with different bank account.",
    )


RULES = [
    check_dup_pan,
    check_dup_gst,
    check_dup_bank_acct,
    check_pan_reuse_new_bank,
]
