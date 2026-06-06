"""Edge-style workflow tests for Agents 4–6 + duplicate logic (Task 13).

Validates: Requirements 5.3, 6.2, 9.1, 9.2, 10.3.

Each scenario runs the full pipeline against an isolated temp DB (no API key →
deterministic agent fallbacks):

- reused-PAN / new-bank  → rejected, with a communications row + fraud signal.
- shared-bank            → rejected, with a communications row.
- missing bank proof     → pending, with a communications row.

A prior vendor row is seeded where the scenario requires duplicate detection.
``test_workflow.py`` keeps the clean → approved (explanation present, no comms)
case.
"""

import importlib
import json

import pytest
from fastapi.testclient import TestClient

_PAN = "ABCCE1234F"
_GST = "27ABCCE1234F1Z2"
_NAME = "Acme Technologies Private Limited"
_ACCT = "123456789012"
_IFSC = "HDFC0001234"

_FORM = {
    "legal_name": _NAME,
    "address": "1 MG Road, Bengaluru, Karnataka",
    "contact_email": "vendor@acme.com",
    "contact_phone": "+91 9876543210",
    "vendor_type": "company",
    "pan": _PAN,
    "gst": _GST,
    "bank": {"account_number": _ACCT, "ifsc": _IFSC, "account_holder": _NAME},
}

_ALL_DOCS = [
    ("pan", "PAN_CARD", {"pan": _PAN, "name": _NAME}),
    ("gst", "GST_CERTIFICATE", {"gstin": _GST, "legal_name": _NAME}),
    ("bank", "CANCELLED_CHEQUE",
     {"account_number": _ACCT, "ifsc": _IFSC, "account_holder": _NAME}),
]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "test_workflow_agents.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    db_module.init_db()
    import backend.main as main
    importlib.reload(main)

    with TestClient(main.app) as test_client:
        yield test_client, db_module


def _seed_vendor(db_module, *, legal_name, pan, gst, bank_account, ifsc):
    db_module.insert_vendor(db_module.new_id(), legal_name, pan, gst, bank_account, ifsc)


def _create_submission(db_module, docs) -> str:
    submission_id = db_module.new_id()
    db_module.execute(
        "INSERT INTO submissions(submission_id, form_json, status, created_at) VALUES (?,?,?,?)",
        [submission_id, json.dumps(_FORM), "received", db_module.utcnow_iso()],
    )
    for slot, doc_type, extracted in docs:
        document_id = db_module.new_id()
        db_module.execute(
            "INSERT INTO documents("
            "document_id, submission_id, slot, file_path, doc_type, classify_conf, "
            "extracted_json, legible, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                document_id, submission_id, slot,
                f"{submission_id}/{document_id}.pdf", doc_type, 0.95,
                json.dumps(extracted), 1, db_module.utcnow_iso(),
            ],
        )
    return submission_id


def _comms(db_module, submission_id):
    return db_module.get_communications(submission_id)


def test_reused_pan_new_bank_is_rejected_with_communication(env):
    test_client, db_module = env
    # Prior vendor with the SAME PAN but a DIFFERENT bank account.
    _seed_vendor(db_module, legal_name="Old Acme", pan=_PAN, gst="27ZZZZZ9999Z1Z9",
                 bank_account="999999999999", ifsc="HDFC0009999")

    submission_id = _create_submission(db_module, _ALL_DOCS)
    body = test_client.post("/workflow/run", json={"submission_id": submission_id}).json()
    decision = body["decision"]

    assert decision["final_status"] == "rejected"
    # PAN_REUSE_NEW_BANK fired (reject) and the fraud weight is reflected.
    reused = next(r for r in decision["rule_results"] if r["rule_id"] == "PAN_REUSE_NEW_BANK")
    assert reused["outcome"] == "fail"
    assert decision["fraud_risk_score"] >= 60
    # A communications row was persisted for the rejected decision.
    assert len(_comms(db_module, submission_id)) == 1


def test_shared_bank_account_is_rejected_with_communication(env):
    test_client, db_module = env
    # Prior vendor sharing the SAME bank account, different PAN.
    _seed_vendor(db_module, legal_name="Other Vendor", pan="ZZZZZ9999Z", gst="27ZZZZZ9999Z1Z9",
                 bank_account=_ACCT, ifsc=_IFSC)

    submission_id = _create_submission(db_module, _ALL_DOCS)
    body = test_client.post("/workflow/run", json={"submission_id": submission_id}).json()
    decision = body["decision"]

    assert decision["final_status"] == "rejected"
    dup = next(r for r in decision["rule_results"] if r["rule_id"] == "DUP_BANK_ACCT")
    assert dup["outcome"] == "fail"
    assert decision["fraud_risk_score"] >= 60
    assert len(_comms(db_module, submission_id)) == 1


def test_missing_bank_proof_is_pending_with_communication(env):
    test_client, db_module = env
    # No bank proof document → MANDATORY_DOCS_PRESENT fails (pending). GST kept so
    # the reject-severity compliance rule still passes.
    docs = [
        ("pan", "PAN_CARD", {"pan": _PAN, "name": _NAME}),
        ("gst", "GST_CERTIFICATE", {"gstin": _GST, "legal_name": _NAME}),
    ]
    submission_id = _create_submission(db_module, docs)
    body = test_client.post("/workflow/run", json={"submission_id": submission_id}).json()
    decision = body["decision"]

    assert decision["final_status"] == "pending"
    mand = next(r for r in decision["rule_results"] if r["rule_id"] == "MANDATORY_DOCS_PRESENT")
    assert mand["outcome"] == "fail"
    comms = _comms(db_module, submission_id)
    assert len(comms) == 1
    # The pending email requests the missing items (stored as JSON text).
    assert json.loads(comms[0]["requested_items_json"])


def test_upsert_vendor_identity_persists_for_future_runs(env):
    """After a run, the vendor identity is recorded so a later submission detects reuse."""
    test_client, db_module = env

    first = _create_submission(db_module, _ALL_DOCS)
    test_client.post("/workflow/run", json={"submission_id": first})

    # The first submission is now linked to a vendors row.
    sub = db_module.get_submission(first)
    assert sub["vendor_id"] is not None
    vendor = db_module.get_vendor(sub["vendor_id"])
    assert vendor["pan"] == _PAN
    assert vendor["bank_account"] == _ACCT
