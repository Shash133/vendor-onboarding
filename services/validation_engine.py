"""Validation engine — runs all 28 deterministic rules (Implementation.md §5).

``run(submission, documents, extracted, db)`` builds a :class:`RuleContext` and
executes every rule across all categories in this fixed order:

    completeness → pan → gst → bank → name_match → documents → duplicates

It NEVER short-circuits: every rule runs and contributes a ``RuleResult`` so the
audit log shows the full picture. Gating (turning results into a decision) happens
later in scoring (Task 11).

Rule coverage — all 28 rules from Architecture.md Part 6 (id · category · severity):

    completeness.py
       1  FORM_REQUIRED_FIELDS            completeness  pending
       2  CONTACT_VALID                   completeness  warning
       3  MANDATORY_DOCS_PRESENT          completeness  pending
       4  DOC_COUNT_SANITY                completeness  pending
    pan.py
       5  PAN_FORMAT                      pan           reject
       6  PAN_ENTITY_TYPE                 pan           warning
       7  PAN_NAME_MATCH                  pan           pending
       8  PAN_DOC_VS_FORM                 pan           reject
    gst.py
       9  GST_FORMAT                      gst           reject
      10  GST_STATE_CODE                  gst           warning
      11  GST_PAN_LINK                    gst           reject
      12  GST_CHECKSUM                    gst           pending
    bank.py
      13  BANK_IFSC_FORMAT                bank          reject
      14  BANK_ACCT_FORMAT                bank          pending
      15  BANK_HOLDER_MATCH               bank          pending
      16  BANK_DOC_CONSISTENT             bank          reject
    name_match.py
      17  NAME_EXACT_MATCH                name          warning
      18  NAME_FUZZY_MATCH                name          pending
      19  NAME_HARD_MISMATCH              name          reject
    duplicates.py
      20  DUP_PAN                         duplicate     pending
      21  DUP_GST                         duplicate     pending
      22  DUP_BANK_ACCT                   duplicate     reject
      23  PAN_REUSE_NEW_BANK              duplicate     reject
    documents.py
      24  DOC_TYPE_CORRECT                document      pending
      25  DOC_WRONG_ATTACHED              document      pending
      26  DOC_LEGIBLE                     document      pending
      27  DOC_CLASSIFY_CONFIDENCE         document      warning
      28  COMPLIANCE_REGISTRATION_PRESENT compliance    reject
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Optional

from database import db as _db
from models.schemas import RuleResult
from services import audit_service
from services.rules import default_name_match
from services.rules import bank, completeness, duplicates, gst, name_match, pan
from services.rules import documents as documents_rules

# Execution order (grouped). Order within a group is irrelevant (no short-circuit),
# but duplicates run last because they depend on the resolved form/extracted fields.
_RULE_MODULES = [completeness, pan, gst, bank, name_match, documents_rules, duplicates]


@dataclass
class RuleContext:
    """Everything a rule needs, bundled into one object (Implementation.md §5).

    Attributes
    ----------
    form:
        Parsed intake form fields (from ``submissions.form_json``).
    extracted:
        Per-document extracted fields keyed by ``doc_type`` (Agent 2 output),
        e.g. ``{"PAN_CARD": {"pan": "...", "name": "..."}}``.
    documents:
        The list of ``documents`` rows (dicts) for the submission.
    vendor_id:
        The current submission's vendor id (used to exclude self in dup lookups).
    db:
        A db handle exposing ``query_one(sql, params)`` for duplicate lookups.
    name_match_fn:
        Injectable fuzzy name comparator (Task 12 wires in Agent 3 here). Defaults
        to the deterministic rapidfuzz implementation.
    """

    form: dict
    extracted: dict
    documents: list
    vendor_id: Optional[str]
    db: Any
    name_match_fn: Callable[[Any, Any], dict] = dc_field(default=default_name_match)


def _parse_form(submission: Any) -> dict:
    """Extract the form-field dict from a submission row (or accept a raw dict)."""
    if isinstance(submission, dict) and "form_json" in submission:
        raw = submission.get("form_json")
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return {}
        return raw or {}
    # Already a parsed form dict.
    return submission or {}


def run(
    submission: Any,
    documents: list,
    extracted: dict,
    db: Any,
    name_match_fn: Optional[Callable[[Any, Any], dict]] = None,
) -> list[RuleResult]:
    """Run all 28 rules and return their results (no short-circuit).

    Parameters mirror Implementation.md §5. ``name_match_fn`` is optional so that
    Task 12 can inject Agent 3 without changing any rule code; it defaults to the
    deterministic rapidfuzz matcher.
    """
    vendor_id = submission.get("vendor_id") if isinstance(submission, dict) else None
    ctx = RuleContext(
        form=_parse_form(submission),
        extracted=extracted or {},
        documents=documents or [],
        vendor_id=vendor_id,
        db=db,
        name_match_fn=name_match_fn or default_name_match,
    )

    results: list[RuleResult] = []
    for module in _RULE_MODULES:
        for rule_fn in module.RULES:
            results.append(rule_fn(ctx))
    return results


def persist_validation_results(submission_id: str, rules: list[RuleResult]) -> None:
    """Persist ``validation_results`` rows and log a ``VALIDATION_RULE`` event per non-pass.

    One row is written for every rule (the full audit picture). For each failing or
    warning rule an append-only ``VALIDATION_RULE`` audit event is logged. All SQL
    is parameterized in the existing db.py style; callers (the workflow engine /
    Task 11) invoke this after validation runs.
    """
    for r in rules:
        _db.execute(
            "INSERT INTO validation_results("
            "result_id, submission_id, rule_id, category, severity, outcome, reason, created_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            [
                _db.new_id(),
                submission_id,
                r.rule_id,
                r.category,
                r.severity,
                r.outcome,
                r.reason,
                _db.utcnow_iso(),
            ],
        )
        if r.outcome != "pass":
            audit_service.log_event(
                submission_id,
                "system",
                "VALIDATION_RULE",
                {"rule_id": r.rule_id, "outcome": r.outcome, "reason": r.reason},
            )
