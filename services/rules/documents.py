"""Document & compliance rules (Architecture.md Part 6, rules 24–28).

    24  DOC_TYPE_CORRECT               document    pending  — required slots filled by correct type
    25  DOC_WRONG_ATTACHED             document    pending  — uploaded doc_type != its slot
    26  DOC_LEGIBLE                    document    pending  — classifier/extractor flagged unreadable
    27  DOC_CLASSIFY_CONFIDENCE        document    warning  — classification confidence >= 0.6
    28  COMPLIANCE_REGISTRATION_PRESENT compliance reject   — valid tax-registration evidence present

Rule 28 is the lone Compliance-category rule; there is no separate compliance
module in the design's file layout, so it lives here (it is about document
evidence). Its ``category`` is still reported as ``compliance`` per Architecture.md.

Pure functions ``(ctx) -> RuleResult``; no short-circuit. See Implementation.md §5.
"""

from __future__ import annotations

from models.schemas import RuleResult

from . import result, slot_to_type

CATEGORY = "document"

# Confidence floor for Agent 1 classification (rule 27 / config CLASSIFY_CONFIDENCE_THRESHOLD).
_CONFIDENCE_FLOOR = 0.6

# Slots that must be present and correctly typed for rule 24.
_REQUIRED_SLOTS = ("pan", "gst", "bank")


def _slot_matches(slot: str | None, doc_type: str | None) -> bool:
    """True if ``doc_type`` satisfies the expected type(s) for ``slot``."""
    expected = slot_to_type(slot)
    if expected is None:
        return True  # unknown slot is not checked
    if isinstance(expected, set):
        return doc_type in expected
    return doc_type == expected


def check_doc_type_correct(ctx) -> RuleResult:
    """Rule 24 — each required slot is filled by a correctly-typed document."""
    missing = []
    for slot in _REQUIRED_SLOTS:
        docs_for_slot = [d for d in ctx.documents if (d.get("slot") or "").strip().lower() == slot]
        if not docs_for_slot or not any(_slot_matches(slot, d.get("doc_type")) for d in docs_for_slot):
            missing.append(slot)
    ok = not missing
    return result(
        "DOC_TYPE_CORRECT", CATEGORY, "pending", ok,
        "All required slots filled by the correct document type."
        if ok else f"Slots missing a correctly-typed document: {missing}.",
    )


def check_wrong_attached(ctx) -> RuleResult:
    """Rule 25 — flag documents whose doc_type does not match their slot."""
    bad = [
        f"{(d.get('slot'))}→{d.get('doc_type')}"
        for d in ctx.documents
        if d.get("slot") and not _slot_matches(d.get("slot"), d.get("doc_type"))
    ]
    ok = not bad
    return result(
        "DOC_WRONG_ATTACHED", CATEGORY, "pending", ok,
        "Every document matches the slot it was uploaded for."
        if ok else f"Wrong document attached for slot(s): {bad}.",
    )


def check_doc_legible(ctx) -> RuleResult:
    """Rule 26 — no document was flagged unreadable (legible == 0)."""
    illegible = [d.get("document_id") or d.get("file_path") for d in ctx.documents if d.get("legible") == 0]
    ok = not illegible
    return result(
        "DOC_LEGIBLE", CATEGORY, "pending", ok,
        "All documents are legible." if ok else f"Illegible document(s): {illegible}.",
    )


def check_doc_classify_confidence(ctx) -> RuleResult:
    """Rule 27 — classification confidence is at least 0.6 (informational).

    Documents not yet classified (``classify_conf`` is None) are not penalised.
    """
    low = [
        f"{d.get('document_id') or d.get('file_path')} ({d.get('classify_conf')})"
        for d in ctx.documents
        if d.get("classify_conf") is not None and d.get("classify_conf") < _CONFIDENCE_FLOOR
    ]
    ok = not low
    return result(
        "DOC_CLASSIFY_CONFIDENCE", CATEGORY, "warning", ok,
        "All classifications meet the confidence floor."
        if ok else f"Low-confidence classification(s): {low}.",
    )


def check_compliance_registration_present(ctx) -> RuleResult:
    """Rule 28 — valid tax-registration evidence is present (reject on fail).

    Simplest interpretation consistent with both docs: a GST certificate (the tax
    registration document for an Indian vendor) must be among the uploaded docs.
    """
    types = {d.get("doc_type") for d in ctx.documents}
    ok = "GST_CERTIFICATE" in types
    return result(
        "COMPLIANCE_REGISTRATION_PRESENT", "compliance", "reject", ok,
        "Tax registration evidence (GST certificate) present."
        if ok else "No tax registration evidence (GST certificate) found.",
    )


RULES = [
    check_doc_type_correct,
    check_wrong_attached,
    check_doc_legible,
    check_doc_classify_confidence,
    check_compliance_registration_present,
]
