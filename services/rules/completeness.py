"""Completeness rules (Architecture.md Part 6, rules 1–4).

    1  FORM_REQUIRED_FIELDS   completeness  pending  — all required fields present
    2  CONTACT_VALID          completeness  warning  — email/phone well-formed
    3  MANDATORY_DOCS_PRESENT completeness  pending  — PAN + tax cert + bank proof
    4  DOC_COUNT_SANITY       completeness  pending  — >= 3 docs, no zero-byte files

Pure functions ``(ctx) -> RuleResult``; no short-circuit. See Implementation.md §5.
"""

from __future__ import annotations

import os
import re

from models.schemas import RuleResult

from . import get_field, result

CATEGORY = "completeness"

# Required intake fields (Implementation.md §5 completeness.py).
REQUIRED_FIELDS = [
    "legal_name",
    "address",
    "contact_email",
    "pan",
    "gst",
    "bank.account_number",
    "bank.ifsc",
]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Indian phone: optional +country code, then 10 digits (separators tolerated).
_PHONE_RE = re.compile(r"^\+?\d[\d\s-]{8,14}\d$")


def check_required_fields(ctx) -> RuleResult:
    """Rule 1 — every required form field is present and non-empty."""
    missing = [f for f in REQUIRED_FIELDS if not get_field(ctx.form, f)]
    return result(
        "FORM_REQUIRED_FIELDS",
        CATEGORY,
        "pending",
        not missing,
        f"Missing required fields: {missing}" if missing else "All required fields present.",
    )


def check_contact_valid(ctx) -> RuleResult:
    """Rule 2 — contact email and phone are well-formed (informational)."""
    email = get_field(ctx.form, "contact_email") or ""
    phone = get_field(ctx.form, "contact_phone") or ""
    email_ok = bool(_EMAIL_RE.match(email))
    phone_ok = bool(_PHONE_RE.match(str(phone)))
    if email_ok and phone_ok:
        return result("CONTACT_VALID", CATEGORY, "warning", True, "Email and phone are well-formed.")
    problems = []
    if not email_ok:
        problems.append("email")
    if not phone_ok:
        problems.append("phone")
    return result("CONTACT_VALID", CATEGORY, "warning", False, f"Malformed contact field(s): {problems}.")


def check_mandatory_docs(ctx) -> RuleResult:
    """Rule 3 — PAN card + GST certificate + bank proof (cheque or letter) present."""
    types = {d.get("doc_type") for d in ctx.documents}
    has_pan = "PAN_CARD" in types
    has_gst = "GST_CERTIFICATE" in types
    has_bank = bool({"CANCELLED_CHEQUE", "BANK_LETTER"} & types)
    ok = has_pan and has_gst and has_bank
    if ok:
        return result("MANDATORY_DOCS_PRESENT", CATEGORY, "pending", True, "All mandatory documents present.")
    missing = []
    if not has_pan:
        missing.append("PAN_CARD")
    if not has_gst:
        missing.append("GST_CERTIFICATE")
    if not has_bank:
        missing.append("bank proof (CANCELLED_CHEQUE or BANK_LETTER)")
    return result("MANDATORY_DOCS_PRESENT", CATEGORY, "pending", False, f"Missing mandatory documents: {missing}.")


def check_doc_count_sanity(ctx) -> RuleResult:
    """Rule 4 — at least 3 documents uploaded and none are zero-byte files."""
    zero_byte = []
    for d in ctx.documents:
        fp = d.get("file_path")
        if fp and os.path.exists(fp):
            try:
                if os.path.getsize(fp) == 0:
                    zero_byte.append(fp)
            except OSError:
                pass
    enough = len(ctx.documents) >= 3
    ok = enough and not zero_byte
    if ok:
        return result("DOC_COUNT_SANITY", CATEGORY, "pending", True, f"{len(ctx.documents)} documents, none empty.")
    reason = []
    if not enough:
        reason.append(f"only {len(ctx.documents)} document(s) (need >= 3)")
    if zero_byte:
        reason.append(f"zero-byte files: {zero_byte}")
    return result("DOC_COUNT_SANITY", CATEGORY, "pending", False, "; ".join(reason) + ".")


# Ordered rule functions for this category (used by the engine).
RULES = [
    check_required_fields,
    check_contact_valid,
    check_mandatory_docs,
    check_doc_count_sanity,
]
