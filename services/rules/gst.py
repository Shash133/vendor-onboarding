"""GST rules (Architecture.md Part 6, rules 9–12).

    9   GST_FORMAT      gst  reject   — 15-char GSTIN pattern valid
    10  GST_STATE_CODE  gst  warning  — first 2 digits a valid state code (01–37)
    11  GST_PAN_LINK    gst  reject   — chars 3–12 of GSTIN == PAN
    12  GST_CHECKSUM    gst  pending  — 15th GSTIN checksum digit valid

Pure functions ``(ctx) -> RuleResult``; no short-circuit. See Implementation.md §5.
"""

from __future__ import annotations

import re

from models.schemas import RuleResult

from . import get_field, result

CATEGORY = "gst"

GST_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$")

# Valid GST state codes are 01–37 (and a few newer UT codes). 01–37 covers the
# design's requirement; full-address matching is out of scope for a warning rule.
_VALID_STATE_CODES = {f"{i:02d}" for i in range(1, 38)}

# GSTIN checksum alphabet (base-36): digits then A–Z.
_CHECK_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _form_gst(ctx) -> str:
    return (get_field(ctx.form, "gst") or "").strip().upper()


def _gstin_checksum(gstin14: str) -> str:
    """Compute the GSTIN check digit for the first 14 characters (base-36)."""
    factor = 2
    total = 0
    for ch in reversed(gstin14):
        digit = _CHECK_CHARS.index(ch)
        addend = factor * digit
        factor = 1 if factor == 2 else 2
        addend = (addend // 36) + (addend % 36)
        total += addend
    return _CHECK_CHARS[(36 - (total % 36)) % 36]


def check_gst_format(ctx) -> RuleResult:
    """Rule 9 — GSTIN matches the canonical 15-char pattern (reject on fail)."""
    gst = _form_gst(ctx)
    ok = bool(GST_RE.match(gst))
    return result(
        "GST_FORMAT", CATEGORY, "reject", ok,
        f"GSTIN '{gst}' is valid." if ok else f"GSTIN '{gst}' does not match the 15-char pattern.",
    )


def check_gst_state_code(ctx) -> RuleResult:
    """Rule 10 — leading 2 digits are a valid state code (informational)."""
    gst = _form_gst(ctx)
    if len(gst) < 2:
        return result("GST_STATE_CODE", CATEGORY, "warning", False, "GSTIN too short to read state code.")
    code = gst[:2]
    ok = code in _VALID_STATE_CODES
    return result(
        "GST_STATE_CODE", CATEGORY, "warning", ok,
        f"State code '{code}' is valid." if ok else f"State code '{code}' is not a valid GST state code.",
    )


def check_gst_pan_link(ctx) -> RuleResult:
    """Rule 11 — characters 3–12 of the GSTIN equal the PAN (reject on fail)."""
    gst = _form_gst(ctx)
    pan = (get_field(ctx.form, "pan") or "").strip().upper()
    ok = len(gst) == 15 and gst[2:12] == pan
    return result(
        "GST_PAN_LINK", CATEGORY, "reject", ok,
        "GSTIN is derived from the PAN." if ok else f"GSTIN chars 3–12 '{gst[2:12]}' != PAN '{pan}'.",
    )


def check_gst_checksum(ctx) -> RuleResult:
    """Rule 12 — the 15th GSTIN digit matches the computed checksum (pending)."""
    gst = _form_gst(ctx)
    if len(gst) != 15 or any(c not in _CHECK_CHARS for c in gst):
        return result("GST_CHECKSUM", CATEGORY, "pending", False, "GSTIN not 15 valid chars; cannot verify checksum.")
    expected = _gstin_checksum(gst[:14])
    ok = expected == gst[14]
    return result(
        "GST_CHECKSUM", CATEGORY, "pending", ok,
        "GSTIN checksum is valid." if ok else f"GSTIN checksum '{gst[14]}' != expected '{expected}'.",
    )


RULES = [
    check_gst_format,
    check_gst_state_code,
    check_gst_pan_link,
    check_gst_checksum,
]
