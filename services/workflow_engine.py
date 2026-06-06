"""Workflow engine — the pipeline orchestrator (Implementation.md §6).

``run(submission_id, emit=None)`` executes the stages of the vendor-onboarding
pipeline in order, recording a ``workflow_runs`` row per stage (for the live run
view), writing append-only audit events, and optionally emitting a
:class:`~models.schemas.StageEvent` per stage transition via the ``emit``
callback (used by the SSE endpoint).

Exact stage order (Architecture.md Part 2 / Implementation.md §6):

    upload → classify → extract → validate → consistency → risk → decide
           → (communicate) → persist/audit

LIVE AGENTS 1–3 (Task 12)
=========================
The ``classify``, ``extract`` and ``consistency`` stages now run the real Gemini
agents (Architecture.md Part 4):

    - classify → Agent 1 (DocumentClassificationAgent) per document, updating
      ``documents.doc_type`` / ``classify_conf`` / ``legible``.
    - extract  → Agent 2 (DocumentExtractionAgent) per document, updating
      ``documents.extracted_json``; the per-doc fields are collected into an
      ``extracted`` dict keyed by ``doc_type`` for the validation rules.
    - the validation engine's fuzzy name matching uses Agent 3
      (ConsistencyCheckingAgent) as ``ctx.name_match_fn`` (rapidfuzz fallback).

The risk / explanation / communication stages are now live (Task 13):

    - risk        → ``collect_signals`` derives fraud signals from the failing
      duplicate/holder/name rules; Agent 4 (RiskAssessmentAgent) narrates them
      (``RISK_ASSESSED`` audit). The deterministic weight sum from
      ``scoring.compute_scores`` remains the authoritative ``fraud_risk_score``.
    - explanation → Agent 5 (DecisionExplanationAgent) explains EVERY decision;
      the explanation is persisted into the decisions row.
    - communicate → for pending/rejected, Agent 6 (VendorCommunicationAgent)
      drafts a vendor email which is persisted to ``communications``.

After the decision, ``upsert_vendor_identity`` records this submission's vendor
identity so FUTURE submissions can detect duplicate / reused PAN/GST/bank.

DETERMINISTIC WITHOUT AN API KEY
================================
The Gemini client is constructed lazily (:func:`_make_client`) and may be
``None`` when ``GEMINI_API_KEY`` is absent. Every agent inherits the base
failure handling: any failed/empty call (including a missing client) raises
``AgentError`` and the agent returns its deterministic fallback, so the pipeline
always completes. Document bytes are read from the stored ``file_path``; when a
row has no ``file_path`` (e.g. tests inject ``doc_type`` + ``extracted_json``
directly) the agent call is skipped and any pre-stored extraction is reused. This
keeps the happy-path workflow test deterministic in CI with no network/key.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
from typing import Any, Callable, Optional

from agents.base import make_client
from agents.classifier import DocumentClassificationAgent
from agents.communication import VendorCommunicationAgent
from agents.consistency import ConsistencyCheckingAgent
from agents.explanation import DecisionExplanationAgent
from agents.extractor import DocumentExtractionAgent
from agents.risk import RiskAssessmentAgent
from backend.config import FRAUD_WEIGHTS
from database import db
from models.schemas import DecisionOut, RuleResultOut, StageEvent
from services import audit_service, scoring, validation_engine

# Rule IDs that map onto a fraud-signal weight (Architecture.md Part 7). A failing
# rule whose id is one of these contributes a fraud signal of the same ``type``,
# so the scoring weight sum stays correct (the signal types ARE the weight keys).
_SIGNAL_RULE_IDS = frozenset(FRAUD_WEIGHTS.keys())


def run(submission_id: str, emit: Optional[Callable[[StageEvent], None]] = None) -> DecisionOut:
    """Run the full pipeline for ``submission_id`` and return the final decision.

    Parameters
    ----------
    submission_id:
        The submission to process. Must already exist (created by
        ``POST /submissions``) with its documents uploaded.
    emit:
        Optional callback invoked with a :class:`StageEvent` on every stage
        transition (``started`` / ``ok`` / ``error``). Used by the SSE live-run
        endpoint; ``None`` for the synchronous ``POST /workflow/run`` path.

    Raises
    ------
    ValueError
        If the submission does not exist.
    """
    sub = db.get_submission(submission_id)
    if sub is None:
        raise ValueError(f"Submission not found: {submission_id}")

    # Make re-runs idempotent: clear any decision / communications / validation
    # rows from a previous run for this submission before regenerating them.
    # Without this, a second run hits the UNIQUE constraint on
    # ``decisions.submission_id`` (and duplicates validation_results rows).
    db.clear_submission_outputs(submission_id)

    # Lazily construct one Gemini client for all agent stages. None when no API
    # key is configured / construction fails — agents then use their fallbacks.
    client = _make_client()

    def stage(name: str, fn: Callable[[], Any]) -> Any:
        """Run one pipeline stage with run-row bookkeeping, audit + events."""
        t0 = time.monotonic()
        run_id = db.insert_workflow_run_start(submission_id, name)
        _emit(emit, name, "started")
        try:
            out = fn()
            duration_ms = _elapsed_ms(t0)
            summary = _summarize(out)
            db.finish_workflow_run(run_id, "ok", duration_ms, summary)
            audit_service.log_event(submission_id, "system", f"{name.upper()}_OK", _audit_payload(out))
            _emit(emit, name, "ok", duration_ms, summary)
            return out
        except Exception as ex:  # noqa: BLE001 - record, audit, then re-raise
            duration_ms = _elapsed_ms(t0)
            db.finish_workflow_run(run_id, "error", duration_ms, str(ex))
            audit_service.log_event(submission_id, "system", f"{name.upper()}_ERROR", {"error": str(ex)})
            _emit(emit, name, "error", duration_ms, str(ex))
            raise

    # --- Pipeline (exact stage order) ----------------------------------------
    docs = stage("upload", lambda: db.get_documents(submission_id))

    # Agent 1: classify each document (updates docs in-place + persists).
    stage("classify", lambda: _classify_documents(docs, client, submission_id))

    # Agent 2: extract per-doc fields → dict keyed by doc_type (persists).
    extracted = stage("extract", lambda: _extract_documents(docs, client, submission_id))

    # Agent 3: fuzzy name comparator injected into the validation engine.
    consistency_agent = ConsistencyCheckingAgent(client)
    name_match_fn = consistency_agent.as_name_match_fn(submission_id)

    # Real validation against the REAL submission form + live extraction.
    rules = stage(
        "validate",
        lambda: validation_engine.run(sub, docs, extracted, db, name_match_fn=name_match_fn),
    )
    rules += stage("consistency", lambda: _consistency_stage())

    # Risk stage: deterministic signal collection + Agent 4 narrative/audit.
    # The weight sum from scoring (below) remains the score source of truth.
    signals = stage("risk", lambda: _risk_stage(rules, client, submission_id))

    # Real scoring + decision gate (Task 11, Architecture.md Part 7).
    scores = scoring.compute_scores(rules, signals)
    status = stage("decide", lambda: scoring.decide(rules, scores))

    # --- Persist / audit ------------------------------------------------------
    validation_engine.persist_validation_results(submission_id, rules)

    # Agent 5: explanation for EVERY decision (deterministic fallback w/o key).
    failing_rules = [r for r in rules if r.outcome != "pass"]
    explanation = DecisionExplanationAgent(client).run(
        status, scores, failing_rules, submission_id=submission_id
    )
    decision_id = db.insert_decision(submission_id, scores, status, explanation)

    # Agent 6: communication for pending/rejected (skipped for approved).
    if status != "approved":
        form = _form_fields(sub)
        comm = stage(
            "communicate",
            lambda: VendorCommunicationAgent(client).run(
                status,
                missing_items(rules),
                form.get("legal_name"),
                form.get("contact_email"),
                submission_id=submission_id,
            ),
        )
        db.insert_communication(decision_id, comm)

    # Persist the vendor identity so FUTURE submissions can detect dup/reuse.
    upsert_vendor_identity(sub)

    db.set_submission_status(submission_id, "decided")
    audit_service.log_event(submission_id, "system", "DECISION_GENERATED", {"status": status, **scores})

    return DecisionOut(
        final_status=status,
        completeness_score=scores["completeness_score"],
        consistency_score=scores["consistency_score"],
        compliance_score=scores["compliance_score"],
        fraud_risk_score=scores["fraud_risk_score"],
        explanation=explanation,
        rule_results=[
            RuleResultOut(
                rule_id=r.rule_id,
                category=r.category,
                severity=r.severity,
                outcome=r.outcome,
                reason=r.reason,
            )
            for r in rules
        ],
    )


# --- Agent client + file helpers ----------------------------------------------
def _make_client() -> Any:
    """Construct a Gemini client lazily; return ``None`` if unavailable.

    Returns ``None`` when ``GEMINI_API_KEY`` is not configured (or client
    construction fails). A ``None`` client makes every agent call fail fast and
    fall back to its deterministic backup, so the pipeline still completes
    without network access — which keeps tests runnable in CI.
    """
    try:
        return make_client()
    except Exception:  # noqa: BLE001 - missing key / construction error → fallbacks
        return None


def _read_file_bytes(file_path: Optional[str]) -> Optional[bytes]:
    """Read a stored document's bytes, or ``None`` when absent/unreadable.

    A missing ``file_path`` (e.g. test rows that inject extraction directly) or
    an unreadable file returns ``None`` so the caller skips the agent gracefully.
    """
    if not file_path:
        return None
    try:
        with open(file_path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _guess_mime(file_path: Optional[str]) -> str:
    """Best-effort MIME type for a stored file (defaults to octet-stream)."""
    mime, _ = mimetypes.guess_type(file_path or "")
    return mime or "application/octet-stream"


def _flatten_extracted(out: Any) -> dict:
    """Flatten Agent 2 output to ``{field: value}`` for the validation rules.

    Agent 2 emits ``{"fields": {"<name>": {"value", "confidence", "verbatim"}},
    "unreadable_fields": [...]}``. The rules consume a flat ``{name: value}`` map
    per doc_type, so collapse each field to its ``value``. An already-flat dict
    (e.g. test-injected extraction) is passed through unchanged.
    """
    if not isinstance(out, dict):
        return {}
    fields = out.get("fields")
    if isinstance(fields, dict):
        return {k: (v.get("value") if isinstance(v, dict) else v) for k, v in fields.items()}
    return {k: v for k, v in out.items() if k != "unreadable_fields"}


# --- Agent stages (Task 12) ---------------------------------------------------
def _classify_documents(docs: list[dict], client: Any, submission_id: str) -> dict:
    """Agent 1 stage: classify each document, updating rows in-place + the DB.

    Documents whose bytes are unavailable (no ``file_path`` / unreadable) keep
    their existing ``doc_type`` and are skipped — this is the deterministic test
    path where ``doc_type`` is injected directly on the row.
    """
    agent = DocumentClassificationAgent(client)
    classified = 0
    for d in docs:
        data = _read_file_bytes(d.get("file_path"))
        if data is None:
            continue
        res = agent.run(
            data,
            _guess_mime(d.get("file_path")),
            os.path.basename(d.get("file_path") or ""),
            submission_id=submission_id,
        )
        doc_type = res.get("doc_type")
        conf = res.get("confidence")
        legible = 1 if res.get("legible", True) else 0
        d["doc_type"] = doc_type
        d["classify_conf"] = conf
        d["legible"] = legible
        if d.get("document_id"):
            db.update_document_classification(d["document_id"], doc_type, conf, legible)
        classified += 1
    return {"classified": classified, "total": len(docs)}


def _extract_documents(docs: list[dict], client: Any, submission_id: str) -> dict:
    """Agent 2 stage: extract per-doc fields → dict keyed by ``doc_type``.

    For documents with readable bytes the extraction agent runs and its raw
    output is persisted to ``documents.extracted_json``. For rows without bytes
    any pre-stored ``extracted_json`` (test-injected) is reused. The collected
    fields are flattened so the validation rules can read them by doc_type.
    """
    agent = DocumentExtractionAgent(client)
    extracted: dict[str, dict] = {}
    for d in docs:
        doc_type = d.get("doc_type")
        if not doc_type or doc_type == "OTHER":
            continue
        data = _read_file_bytes(d.get("file_path"))
        if data is not None:
            out = agent.run(data, _guess_mime(d.get("file_path")), doc_type, submission_id=submission_id)
            if d.get("document_id"):
                db.update_document_extraction(d["document_id"], json.dumps(out))
        else:
            raw = d.get("extracted_json")
            if not raw:
                continue
            out = json.loads(raw) if isinstance(raw, str) else raw
        flat = _flatten_extracted(out)
        if flat:
            extracted[doc_type] = {**extracted.get(doc_type, {}), **flat}
    return extracted


def _consistency_stage() -> list:
    """Consistency stage: the fuzzy name rules already run inside the validation
    engine via Agent 3 (injected as ``name_match_fn``), so no extra rules here."""
    return []


def _risk_stage(rules: list, client: Any, submission_id: str) -> list:
    """Risk stage: collect deterministic fraud signals + run Agent 4 narrative.

    The returned ``signals`` feed ``scoring.compute_scores`` (the weight sum is
    the authoritative ``fraud_risk_score``). Agent 4 is invoked purely for the
    reviewer-facing narrative and its ``RISK_ASSESSED`` audit event; its emitted
    score never overrides scoring.
    """
    signals = collect_signals(rules)
    # Narrative + audit only (deterministic fallback when no API key).
    RiskAssessmentAgent(client).run(signals, submission_id=submission_id)
    return signals


def collect_signals(rules: list) -> list[dict]:
    """Derive fraud signals from failing duplicate / holder / name rules.

    Each failing rule whose ``rule_id`` is a ``FRAUD_WEIGHTS`` key (DUP_BANK_ACCT,
    PAN_REUSE_NEW_BANK, DUP_PAN, DUP_GST, BANK_HOLDER_MATCH, NAME_HARD_MISMATCH)
    becomes a ``{"type", "detail"}`` signal whose ``type`` matches the weight key,
    so the scoring weight sum is correct by construction.
    """
    return [
        {"type": r.rule_id, "detail": r.reason}
        for r in (rules or [])
        if r.outcome == "fail" and r.rule_id in _SIGNAL_RULE_IDS
    ]


def missing_items(rules: list) -> list[str]:
    """Human-readable list of what the vendor must fix (failing pending rules).

    Used to build the vendor communication. Reject-severity failures are excluded
    here (the rejection email never reveals internal fraud heuristics).
    """
    return [
        f"{r.rule_id}: {r.reason}"
        for r in (rules or [])
        if r.outcome == "fail" and r.severity == "pending"
    ]


def _form_fields(submission: Any) -> dict:
    """Parse the submission's ``form_json`` into a dict (best effort)."""
    if not isinstance(submission, dict):
        return {}
    raw = submission.get("form_json")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw or {}


def upsert_vendor_identity(submission: Any) -> str:
    """Insert/update the vendors row for this submission's identity.

    Resolves a stable vendor identity so FUTURE submissions can detect duplicate
    or reused PAN/GST/bank accounts:

    - First run (submission has no ``vendor_id``): insert a fresh vendors row and
      link the submission to it.
    - Re-run (submission already linked): update that same vendors row in place,
      so re-processing is idempotent and never creates a false self-duplicate
      (the duplicate rules already exclude ``ctx.vendor_id``).

    A prior, different vendor that happens to share the PAN is left untouched, so
    the reused-PAN fraud signal is preserved across runs.
    """
    form = _form_fields(submission)
    submission_id = submission.get("submission_id") if isinstance(submission, dict) else None
    legal_name = form.get("legal_name") or ""
    pan = (form.get("pan") or None)
    gst = (form.get("gst") or None)
    bank = form.get("bank") or {}
    bank_account = bank.get("account_number") if isinstance(bank, dict) else None
    ifsc = bank.get("ifsc") if isinstance(bank, dict) else None

    vendor_id = submission.get("vendor_id") if isinstance(submission, dict) else None
    if vendor_id:
        db.update_vendor(vendor_id, legal_name, pan, gst, bank_account, ifsc)
        return vendor_id

    vendor_id = db.new_id()
    db.insert_vendor(vendor_id, legal_name, pan, gst, bank_account, ifsc)
    if submission_id:
        db.set_submission_vendor(submission_id, vendor_id)
    return vendor_id


# --- Stage event / summary helpers --------------------------------------------
def _emit(
    emit: Optional[Callable[[StageEvent], None]],
    stage: str,
    status: str,
    duration_ms: Optional[int] = None,
    summary: Optional[str] = None,
) -> None:
    """Invoke the optional SSE callback with a StageEvent (no-op when emit is None)."""
    if emit is None:
        return
    emit(StageEvent(stage=stage, status=status, duration_ms=duration_ms, summary=summary))


def _elapsed_ms(t0: float) -> int:
    """Whole milliseconds elapsed since the monotonic timestamp ``t0``."""
    return int((time.monotonic() - t0) * 1000)


def _summarize(out: Any) -> str:
    """Build a short human-readable ``output_summary`` for a stage result."""
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    if isinstance(out, list):
        return f"{len(out)} item(s)"
    if isinstance(out, dict):
        return json.dumps(out)[:200]
    return str(out)[:200]


def _audit_payload(out: Any) -> dict:
    """Coerce a stage result into a dict payload for the (JSON) audit log."""
    if isinstance(out, dict):
        return out
    if isinstance(out, list):
        return {"count": len(out)}
    return {"value": out}
