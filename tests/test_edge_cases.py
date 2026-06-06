"""End-to-end edge-case tests for all 10 §9 scenarios (Task 19).

Validates: Requirements 9.1, 9.2.

Each of the 10 edge cases from Implementation.md §9 (and the Architecture.md
Part 8 definitions that GOVERN the design) is driven through the REAL pipeline
(``services.workflow_engine.run``) against an isolated temporary SQLite DB, then
asserted on three things:

    1. the persisted final status matches the §9 ``expected_status``;
    2. the §9 ``expected_failing_rule`` is present with ``outcome == "fail"`` in
       the persisted validation_results (for the cases that name one); and
    3. a communications row exists for the pending/rejected cases (2–7, 9, 10)
       and does NOT exist for the approved cases (1, 8).

Determinism without a live Gemini key
--------------------------------------
Like the rest of the workflow suite, each document row is created with a
NON-EXISTENT ``file_path`` and a pre-populated ``doc_type`` / ``classify_conf``
/ ``legible`` / ``extracted_json``. With no readable bytes the classify/extract
agents skip the (key-less) model call and the injected values flow straight into
the validation engine. The per-document ``extracted_json`` is the flat
``{field: value}`` map the engine produces after ``_flatten_extracted``; the
extract stage keys each doc's fields by its ``doc_type`` exactly as in a live run.

Cases 9 & 10 seed the prior vendors first via ``database.seed.seed()`` so the
duplicate / reuse rules have a counterpart to match against.

The fixtures manifest (``fixtures.generate_fixtures.get_manifest``) is the single
source of truth for every case's form, documents, expected status, and expected
failing rule — the test is fully parametrized over it.
"""

from __future__ import annotations

import importlib
import json

import pytest

# Cases whose decision is pending/rejected MUST have a vendor communication row;
# the approved cases (1, 8) MUST NOT (Architecture.md Part 8 / Implementation.md §9).
_APPROVED_CASES = {1, 8}
# Cases that rely on a seeded prior vendor for duplicate / reuse detection.
_SEED_CASES = {9, 10}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated temp DB using the standard reload pattern (see tests/test_db.py).

    Point DB_PATH at a fresh file, reload backend.config then database.db so the
    patched path is picked up everywhere, init the schema, and hand back the
    reloaded db module. Each test (each parametrized case) gets its own DB.
    """
    db_file = tmp_path / "test_edge_cases.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    db_module.init_db()

    return db_module


def _build_manifest():
    """(Re)generate the fixtures and return the §9 manifest (10 cases)."""
    from fixtures import generate_fixtures
    return generate_fixtures.get_manifest()


# Generate once at import time; parametrize the test over the 10 cases.
_MANIFEST = _build_manifest()
_CASE_IDS = [f"case_{entry['case']}_{entry['expected_status']}" for entry in _MANIFEST]


def _create_submission_from_manifest(db_module, entry: dict) -> str:
    """Insert a submission + its documents from a manifest entry.

    Documents carry their ``doc_type`` / ``classify_conf`` / ``legible`` and a
    pre-stored flat ``extracted_json`` (the manifest's extracted fields). The
    ``file_path`` points at a non-existent file so the classify/extract agents
    skip the model call and reuse the injected values — fully deterministic with
    no API key.
    """
    submission_id = db_module.new_id()
    db_module.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at) VALUES (?,?,?,?)",
        [submission_id, json.dumps(entry["form"]), "received", db_module.utcnow_iso()],
    )
    for doc in entry["documents"]:
        document_id = db_module.new_id()
        legible = 1 if doc.get("legible", True) else 0
        db_module.execute(
            "INSERT INTO documents("
            "document_id, submission_id, slot, file_path, doc_type, classify_conf, "
            "extracted_json, legible, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                document_id,
                submission_id,
                doc["slot"],
                f"{submission_id}/{document_id}.pdf",  # non-existent → agents skip
                doc["doc_type"],
                0.95,
                json.dumps(doc["extracted"]),
                legible,
                db_module.utcnow_iso(),
            ],
        )
    return submission_id


@pytest.mark.parametrize("entry", _MANIFEST, ids=_CASE_IDS)
def test_edge_case_end_to_end(env, entry):
    """Run one §9 edge case through the real pipeline and assert its outcome."""
    db_module = env
    case_no = entry["case"]

    # Cases 9 & 10 need the prior vendors seeded so dup/reuse rules can fire.
    if case_no in _SEED_CASES:
        from database import seed
        seed.seed()

    submission_id = _create_submission_from_manifest(db_module, entry)

    from services import workflow_engine
    decision = workflow_engine.run(submission_id)

    # 1) Final status matches the §9 table (assert on both the returned decision
    #    and the persisted decisions row).
    assert decision.final_status == entry["expected_status"], (
        f"case {case_no}: expected {entry['expected_status']}, got {decision.final_status} "
        f"(failing rules: {[r.rule_id for r in decision.rule_results if r.outcome == 'fail']})"
    )
    persisted = db_module.get_decision(submission_id)
    assert persisted is not None
    assert persisted["final_status"] == entry["expected_status"]

    # 2) The expected headline failing rule is present and failing in the
    #    persisted validation_results (for the cases that name one).
    expected_rule = entry["expected_failing_rule"]
    results = db_module.get_validation_results(submission_id)
    assert len(results) == 28, f"case {case_no}: expected 28 rule rows, got {len(results)}"
    by_id = {r["rule_id"]: r for r in results}
    if expected_rule is not None:
        assert expected_rule in by_id, f"case {case_no}: {expected_rule} not in results"
        assert by_id[expected_rule]["outcome"] == "fail", (
            f"case {case_no}: {expected_rule} expected to FAIL, got "
            f"{by_id[expected_rule]['outcome']} ({by_id[expected_rule]['reason']})"
        )
    else:
        # Approved cases name no failing rule → no rule should be a hard failure.
        failures = [r["rule_id"] for r in results if r["outcome"] == "fail"]
        assert not failures, f"case {case_no}: approved case has failing rules {failures}"

    # 3) Communications presence: pending/rejected → exactly one row; approved → none.
    comms = db_module.get_communications(submission_id)
    if case_no in _APPROVED_CASES:
        assert comms == [], f"case {case_no}: approved case should have no communications"
    else:
        assert len(comms) == 1, (
            f"case {case_no}: {entry['expected_status']} case should have one communication, "
            f"got {len(comms)}"
        )


def test_manifest_covers_all_ten_cases():
    """Sanity: the fixtures manifest enumerates exactly the 10 §9 cases."""
    assert [entry["case"] for entry in _MANIFEST] == list(range(1, 11))
