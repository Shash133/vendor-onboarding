"""Tests for the validation engine and all 28 rules (Task 10, Requirement 3.1).

Each rule has a PASS case and a FAIL case asserting rule_id, severity, and outcome.
Rules that do not touch the database are exercised by calling the pure rule
function directly with a hand-built context. Duplicate rules (20–23) and the
``persist_validation_results`` helper use a real SQLite database (temp file) with
seeded prior vendors, per Implementation.md §5.
"""

from __future__ import annotations

import copy
import importlib

import pytest

from services.rules import bank, completeness, documents, duplicates, gst, name_match, pan
from services.rules.gst import _gstin_checksum
from services.validation_engine import RuleContext

# --- Clean baseline fixtures (everything passes) ------------------------------
CLEAN_PAN = "ABCCE1234F"  # 4th char 'C' = company
CLEAN_GST = "27" + CLEAN_PAN + "1Z" + _gstin_checksum("27" + CLEAN_PAN + "1Z")
CLEAN_NAME = "Acme Technologies Private Limited"
CLEAN_ACCT = "123456789012"
CLEAN_IFSC = "HDFC0001234"

CLEAN_FORM = {
    "legal_name": CLEAN_NAME,
    "address": "1 MG Road, Bengaluru, Karnataka",
    "contact_email": "vendor@acme.com",
    "contact_phone": "+91 9876543210",
    "vendor_type": "company",
    "pan": CLEAN_PAN,
    "gst": CLEAN_GST,
    "bank": {"account_number": CLEAN_ACCT, "ifsc": CLEAN_IFSC, "account_holder": CLEAN_NAME},
}

CLEAN_EXTRACTED = {
    "PAN_CARD": {"pan": CLEAN_PAN, "name": CLEAN_NAME},
    "GST_CERTIFICATE": {"gstin": CLEAN_GST, "legal_name": CLEAN_NAME},
    "CANCELLED_CHEQUE": {"account_number": CLEAN_ACCT, "ifsc": CLEAN_IFSC, "account_holder": CLEAN_NAME},
}

CLEAN_DOCS = [
    {"document_id": "d1", "slot": "pan", "doc_type": "PAN_CARD", "legible": 1, "classify_conf": 0.95, "file_path": None},
    {"document_id": "d2", "slot": "gst", "doc_type": "GST_CERTIFICATE", "legible": 1, "classify_conf": 0.95, "file_path": None},
    {"document_id": "d3", "slot": "bank", "doc_type": "CANCELLED_CHEQUE", "legible": 1, "classify_conf": 0.95, "file_path": None},
]


def make_ctx(*, form=None, extracted=None, docs=None, db=None, vendor_id=None) -> RuleContext:
    """Build a RuleContext from deep-copied clean defaults with optional overrides."""
    return RuleContext(
        form=copy.deepcopy(form if form is not None else CLEAN_FORM),
        extracted=copy.deepcopy(extracted if extracted is not None else CLEAN_EXTRACTED),
        documents=copy.deepcopy(docs if docs is not None else CLEAN_DOCS),
        vendor_id=vendor_id,
        db=db,
    )


def assert_rule(res, rule_id, severity, outcome):
    assert res.rule_id == rule_id, f"expected {rule_id}, got {res.rule_id}"
    assert res.severity == severity, f"{rule_id}: expected severity {severity}, got {res.severity}"
    assert res.outcome == outcome, f"{rule_id}: expected outcome {outcome}, got {res.outcome} ({res.reason})"


# ============================== COMPLETENESS (1–4) ============================
def test_rule1_required_fields_pass():
    assert_rule(completeness.check_required_fields(make_ctx()), "FORM_REQUIRED_FIELDS", "pending", "pass")


def test_rule1_required_fields_fail():
    form = copy.deepcopy(CLEAN_FORM)
    del form["legal_name"]
    assert_rule(completeness.check_required_fields(make_ctx(form=form)), "FORM_REQUIRED_FIELDS", "pending", "fail")


def test_rule2_contact_valid_pass():
    assert_rule(completeness.check_contact_valid(make_ctx()), "CONTACT_VALID", "warning", "pass")


def test_rule2_contact_valid_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["contact_email"] = "not-an-email"
    assert_rule(completeness.check_contact_valid(make_ctx(form=form)), "CONTACT_VALID", "warning", "warn")


def test_rule3_mandatory_docs_pass():
    assert_rule(completeness.check_mandatory_docs(make_ctx()), "MANDATORY_DOCS_PRESENT", "pending", "pass")


def test_rule3_mandatory_docs_fail():
    docs = [d for d in CLEAN_DOCS if d["doc_type"] != "GST_CERTIFICATE"]
    assert_rule(completeness.check_mandatory_docs(make_ctx(docs=docs)), "MANDATORY_DOCS_PRESENT", "pending", "fail")


def test_rule4_doc_count_pass():
    assert_rule(completeness.check_doc_count_sanity(make_ctx()), "DOC_COUNT_SANITY", "pending", "pass")


def test_rule4_doc_count_fail():
    assert_rule(completeness.check_doc_count_sanity(make_ctx(docs=CLEAN_DOCS[:1])), "DOC_COUNT_SANITY", "pending", "fail")


# ================================== PAN (5–8) ================================
def test_rule5_pan_format_pass():
    assert_rule(pan.check_pan_format(make_ctx()), "PAN_FORMAT", "reject", "pass")


def test_rule5_pan_format_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["pan"] = "BAD123"
    assert_rule(pan.check_pan_format(make_ctx(form=form)), "PAN_FORMAT", "reject", "fail")


def test_rule6_pan_entity_type_pass():
    assert_rule(pan.check_pan_entity_type(make_ctx()), "PAN_ENTITY_TYPE", "warning", "pass")


def test_rule6_pan_entity_type_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["pan"] = "ABCPE1234F"  # 4th char 'P' != 'C' for company
    assert_rule(pan.check_pan_entity_type(make_ctx(form=form)), "PAN_ENTITY_TYPE", "warning", "warn")


def test_rule7_pan_name_match_pass():
    assert_rule(pan.check_pan_name_match(make_ctx()), "PAN_NAME_MATCH", "pending", "pass")


def test_rule7_pan_name_match_fail():
    extracted = copy.deepcopy(CLEAN_EXTRACTED)
    extracted["PAN_CARD"]["name"] = "Different Holder Name"
    assert_rule(pan.check_pan_name_match(make_ctx(extracted=extracted)), "PAN_NAME_MATCH", "pending", "fail")


def test_rule8_pan_doc_vs_form_pass():
    assert_rule(pan.check_pan_doc_vs_form(make_ctx()), "PAN_DOC_VS_FORM", "reject", "pass")


def test_rule8_pan_doc_vs_form_fail():
    extracted = copy.deepcopy(CLEAN_EXTRACTED)
    extracted["PAN_CARD"]["pan"] = "ZZZZZ9999Z"
    assert_rule(pan.check_pan_doc_vs_form(make_ctx(extracted=extracted)), "PAN_DOC_VS_FORM", "reject", "fail")


def test_rule8_pan_doc_vs_form_missing_card_pan_passes():
    """§9 cases 3 & 4: a missing/unreadable extracted PAN is incompleteness, not a
    present-value contradiction, so the reject-severity rule PASSES (the situation
    is governed by the pending DOC_WRONG_ATTACHED / DOC_LEGIBLE signals instead)."""
    # No PAN_CARD extracted at all (wrong doc in the PAN slot, e.g. case 3).
    assert_rule(pan.check_pan_doc_vs_form(make_ctx(extracted={})), "PAN_DOC_VS_FORM", "reject", "pass")
    # PAN_CARD present but the value is null (illegible scan, e.g. case 4).
    extracted = {"PAN_CARD": {"pan": None, "name": None}}
    assert_rule(pan.check_pan_doc_vs_form(make_ctx(extracted=extracted)), "PAN_DOC_VS_FORM", "reject", "pass")


# ================================== GST (9–12) ===============================
def test_rule9_gst_format_pass():
    assert_rule(gst.check_gst_format(make_ctx()), "GST_FORMAT", "reject", "pass")


def test_rule9_gst_format_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["gst"] = "NOTAGST"
    assert_rule(gst.check_gst_format(make_ctx(form=form)), "GST_FORMAT", "reject", "fail")


def test_rule10_gst_state_code_pass():
    assert_rule(gst.check_gst_state_code(make_ctx()), "GST_STATE_CODE", "warning", "pass")


def test_rule10_gst_state_code_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["gst"] = "99" + CLEAN_PAN + "1Z0"  # state code 99 is invalid
    assert_rule(gst.check_gst_state_code(make_ctx(form=form)), "GST_STATE_CODE", "warning", "warn")


def test_rule11_gst_pan_link_pass():
    assert_rule(gst.check_gst_pan_link(make_ctx()), "GST_PAN_LINK", "reject", "pass")


def test_rule11_gst_pan_link_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["gst"] = "27ZZZZZ9999Z1Z0"  # chars 3-12 != PAN
    assert_rule(gst.check_gst_pan_link(make_ctx(form=form)), "GST_PAN_LINK", "reject", "fail")


def test_rule12_gst_checksum_pass():
    assert_rule(gst.check_gst_checksum(make_ctx()), "GST_CHECKSUM", "pending", "pass")


def test_rule12_gst_checksum_fail():
    form = copy.deepcopy(CLEAN_FORM)
    wrong = "0" if CLEAN_GST[14] != "0" else "1"
    form["gst"] = CLEAN_GST[:14] + wrong
    assert_rule(gst.check_gst_checksum(make_ctx(form=form)), "GST_CHECKSUM", "pending", "fail")


# ================================== BANK (13–16) =============================
def test_rule13_ifsc_format_pass():
    assert_rule(bank.check_ifsc_format(make_ctx()), "BANK_IFSC_FORMAT", "reject", "pass")


def test_rule13_ifsc_format_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["bank"]["ifsc"] = "BADIFSC"
    assert_rule(bank.check_ifsc_format(make_ctx(form=form)), "BANK_IFSC_FORMAT", "reject", "fail")


def test_rule14_bank_acct_format_pass():
    assert_rule(bank.check_bank_acct_format(make_ctx()), "BANK_ACCT_FORMAT", "pending", "pass")


def test_rule14_bank_acct_format_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["bank"]["account_number"] = "12AB"
    assert_rule(bank.check_bank_acct_format(make_ctx(form=form)), "BANK_ACCT_FORMAT", "pending", "fail")


def test_rule15_bank_holder_match_pass():
    assert_rule(bank.check_bank_holder_match(make_ctx()), "BANK_HOLDER_MATCH", "pending", "pass")


def test_rule15_bank_holder_match_fail():
    form = copy.deepcopy(CLEAN_FORM)
    form["bank"]["account_holder"] = "Totally Unrelated Person"
    assert_rule(bank.check_bank_holder_match(make_ctx(form=form)), "BANK_HOLDER_MATCH", "pending", "fail")


def test_rule16_bank_doc_consistent_pass():
    assert_rule(bank.check_bank_doc_consistent(make_ctx()), "BANK_DOC_CONSISTENT", "reject", "pass")


def test_rule16_bank_doc_consistent_fail():
    extracted = copy.deepcopy(CLEAN_EXTRACTED)
    extracted["CANCELLED_CHEQUE"]["account_number"] = "999999999999"
    assert_rule(bank.check_bank_doc_consistent(make_ctx(extracted=extracted)), "BANK_DOC_CONSISTENT", "reject", "fail")


# ================================ NAME MATCH (17–19) =========================
def test_rule17_name_exact_pass():
    assert_rule(name_match.check_name_exact_match(make_ctx()), "NAME_EXACT_MATCH", "warning", "pass")


def test_rule17_name_exact_fail():
    extracted = copy.deepcopy(CLEAN_EXTRACTED)
    extracted["PAN_CARD"]["name"] = "Acme Tech Pvt Ltd"  # not exact after normalise
    assert_rule(name_match.check_name_exact_match(make_ctx(extracted=extracted)), "NAME_EXACT_MATCH", "warning", "warn")


def test_rule18_name_fuzzy_pass():
    assert_rule(name_match.check_name_fuzzy_match(make_ctx()), "NAME_FUZZY_MATCH", "pending", "pass")


def test_rule18_name_fuzzy_fail():
    extracted = copy.deepcopy(CLEAN_EXTRACTED)
    extracted["PAN_CARD"]["name"] = "Zenith Foods Corporation"
    assert_rule(name_match.check_name_fuzzy_match(make_ctx(extracted=extracted)), "NAME_FUZZY_MATCH", "pending", "fail")


def test_rule19_name_hard_mismatch_pass():
    assert_rule(name_match.check_name_hard_mismatch(make_ctx()), "NAME_HARD_MISMATCH", "reject", "pass")


def test_rule19_name_hard_mismatch_fail():
    extracted = {"PAN_CARD": {"pan": CLEAN_PAN, "name": "Zzzz"}}
    assert_rule(name_match.check_name_hard_mismatch(make_ctx(extracted=extracted)), "NAME_HARD_MISMATCH", "reject", "fail")


# ================================ DOCUMENTS (24–28) ==========================
def test_rule24_doc_type_correct_pass():
    assert_rule(documents.check_doc_type_correct(make_ctx()), "DOC_TYPE_CORRECT", "pending", "pass")


def test_rule24_doc_type_correct_fail():
    docs = [d for d in CLEAN_DOCS if d["slot"] != "bank"]  # bank slot missing
    assert_rule(documents.check_doc_type_correct(make_ctx(docs=docs)), "DOC_TYPE_CORRECT", "pending", "fail")


def test_rule25_wrong_attached_pass():
    assert_rule(documents.check_wrong_attached(make_ctx()), "DOC_WRONG_ATTACHED", "pending", "pass")


def test_rule25_wrong_attached_fail():
    docs = copy.deepcopy(CLEAN_DOCS)
    docs[0]["doc_type"] = "GST_CERTIFICATE"  # pan slot holds a GST cert
    assert_rule(documents.check_wrong_attached(make_ctx(docs=docs)), "DOC_WRONG_ATTACHED", "pending", "fail")


def test_rule26_doc_legible_pass():
    assert_rule(documents.check_doc_legible(make_ctx()), "DOC_LEGIBLE", "pending", "pass")


def test_rule26_doc_legible_fail():
    docs = copy.deepcopy(CLEAN_DOCS)
    docs[0]["legible"] = 0
    assert_rule(documents.check_doc_legible(make_ctx(docs=docs)), "DOC_LEGIBLE", "pending", "fail")


def test_rule27_classify_confidence_pass():
    assert_rule(documents.check_doc_classify_confidence(make_ctx()), "DOC_CLASSIFY_CONFIDENCE", "warning", "pass")


def test_rule27_classify_confidence_fail():
    docs = copy.deepcopy(CLEAN_DOCS)
    docs[0]["classify_conf"] = 0.3
    assert_rule(documents.check_doc_classify_confidence(make_ctx(docs=docs)), "DOC_CLASSIFY_CONFIDENCE", "warning", "warn")


def test_rule28_compliance_registration_pass():
    res = documents.check_compliance_registration_present(make_ctx())
    assert res.rule_id == "COMPLIANCE_REGISTRATION_PRESENT"
    assert res.category == "compliance"
    assert res.severity == "reject"
    assert res.outcome == "pass"


def test_rule28_compliance_registration_fail():
    docs = [d for d in CLEAN_DOCS if d["doc_type"] != "GST_CERTIFICATE"]
    res = documents.check_compliance_registration_present(make_ctx(docs=docs))
    assert res.rule_id == "COMPLIANCE_REGISTRATION_PRESENT"
    assert res.severity == "reject"
    assert res.outcome == "fail"


# ============================ DUPLICATES (20–23) — real DB ===================
@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Reload db.py against a temp DB and initialise the schema."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "rules_test.db"))
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    return db_module


def _seed_vendor(db, *, pan=CLEAN_PAN, gst=CLEAN_GST, bank_account=CLEAN_ACCT, vendor_id="existing-vendor"):
    db.execute(
        "INSERT INTO vendors(vendor_id, legal_name, pan, gst, bank_account, ifsc, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        [vendor_id, "Existing Vendor Pvt Ltd", pan, gst, bank_account, CLEAN_IFSC, db.utcnow_iso()],
    )


def test_rule20_dup_pan_pass(db):
    assert_rule(duplicates.check_dup_pan(make_ctx(db=db)), "DUP_PAN", "pending", "pass")


def test_rule20_dup_pan_fail(db):
    _seed_vendor(db)
    assert_rule(duplicates.check_dup_pan(make_ctx(db=db)), "DUP_PAN", "pending", "fail")


def test_rule21_dup_gst_pass(db):
    assert_rule(duplicates.check_dup_gst(make_ctx(db=db)), "DUP_GST", "pending", "pass")


def test_rule21_dup_gst_fail(db):
    _seed_vendor(db)
    assert_rule(duplicates.check_dup_gst(make_ctx(db=db)), "DUP_GST", "pending", "fail")


def test_rule22_dup_bank_acct_pass(db):
    assert_rule(duplicates.check_dup_bank_acct(make_ctx(db=db)), "DUP_BANK_ACCT", "reject", "pass")


def test_rule22_dup_bank_acct_fail(db):
    _seed_vendor(db)
    assert_rule(duplicates.check_dup_bank_acct(make_ctx(db=db)), "DUP_BANK_ACCT", "reject", "fail")


def test_rule23_pan_reuse_new_bank_pass(db):
    # Same PAN but SAME bank account → not a reuse-with-new-bank case.
    _seed_vendor(db, bank_account=CLEAN_ACCT)
    assert_rule(duplicates.check_pan_reuse_new_bank(make_ctx(db=db)), "PAN_REUSE_NEW_BANK", "reject", "pass")


def test_rule23_pan_reuse_new_bank_fail(db):
    _seed_vendor(db, bank_account="999999999999")  # same PAN, different bank
    assert_rule(duplicates.check_pan_reuse_new_bank(make_ctx(db=db)), "PAN_REUSE_NEW_BANK", "reject", "fail")


# ============================ ENGINE + PERSISTENCE ===========================
EXPECTED_RULE_IDS = {
    "FORM_REQUIRED_FIELDS", "CONTACT_VALID", "MANDATORY_DOCS_PRESENT", "DOC_COUNT_SANITY",
    "PAN_FORMAT", "PAN_ENTITY_TYPE", "PAN_NAME_MATCH", "PAN_DOC_VS_FORM",
    "GST_FORMAT", "GST_STATE_CODE", "GST_PAN_LINK", "GST_CHECKSUM",
    "BANK_IFSC_FORMAT", "BANK_ACCT_FORMAT", "BANK_HOLDER_MATCH", "BANK_DOC_CONSISTENT",
    "NAME_EXACT_MATCH", "NAME_FUZZY_MATCH", "NAME_HARD_MISMATCH",
    "DUP_PAN", "DUP_GST", "DUP_BANK_ACCT", "PAN_REUSE_NEW_BANK",
    "DOC_TYPE_CORRECT", "DOC_WRONG_ATTACHED", "DOC_LEGIBLE", "DOC_CLASSIFY_CONFIDENCE",
    "COMPLIANCE_REGISTRATION_PRESENT",
}


def test_engine_runs_all_28_rules(db):
    import json
    from services import validation_engine
    importlib.reload(validation_engine)
    submission = {"vendor_id": None, "form_json": json.dumps(CLEAN_FORM)}
    results = validation_engine.run(submission, CLEAN_DOCS, CLEAN_EXTRACTED, db)
    assert len(results) == 28
    assert {r.rule_id for r in results} == EXPECTED_RULE_IDS


def test_engine_clean_submission_all_pass(db):
    import json
    from services import validation_engine
    importlib.reload(validation_engine)
    submission = {"vendor_id": None, "form_json": json.dumps(CLEAN_FORM)}
    results = validation_engine.run(submission, CLEAN_DOCS, CLEAN_EXTRACTED, db)
    non_pass = [(r.rule_id, r.outcome, r.reason) for r in results if r.outcome != "pass"]
    assert non_pass == [], f"clean submission should pass all rules, got: {non_pass}"


def test_persist_validation_results_writes_rows_and_audit(db):
    import json
    from services import validation_engine
    importlib.reload(validation_engine)

    sub_id = db.new_id()
    db.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at) VALUES (?,?,?,?)",
        [sub_id, json.dumps(CLEAN_FORM), "received", db.utcnow_iso()],
    )
    # Force some non-pass rules: bad PAN format + missing GST doc.
    form = copy.deepcopy(CLEAN_FORM)
    form["pan"] = "BAD123"
    docs = [d for d in CLEAN_DOCS if d["doc_type"] != "GST_CERTIFICATE"]
    submission = {"submission_id": sub_id, "vendor_id": None, "form_json": json.dumps(form)}

    results = validation_engine.run(submission, docs, CLEAN_EXTRACTED, db)
    validation_engine.persist_validation_results(sub_id, results)

    rows = db.get_validation_results(sub_id)
    assert len(rows) == 28  # one row per rule (full picture)

    audit_rows = db.query(
        "SELECT * FROM audit_logs WHERE submission_id = ? AND action = 'VALIDATION_RULE'", [sub_id]
    )
    non_pass = [r for r in results if r.outcome != "pass"]
    assert len(audit_rows) == len(non_pass) > 0
