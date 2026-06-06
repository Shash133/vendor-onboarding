"""Tests for fixtures + seed data (Task 16, Implementation.md §1, §9).

Light coverage only — these assert the generator produces a manifest for all 10
edge cases with their files on disk, and that seeding inserts the expected prior
vendors. Running the fixtures through the full pipeline is Task 19's edge-case
suite, not here.
"""

from __future__ import annotations

import importlib
import os

import pytest

VALID_STATUSES = {"approved", "pending", "rejected"}


# --- Fixtures generator -------------------------------------------------------
def test_generate_produces_manifest_for_all_ten_cases(tmp_path):
    from fixtures import generate_fixtures

    manifest = generate_fixtures.generate(output_dir=str(tmp_path))

    # All 10 cases present, numbered 1..10.
    assert [entry["case"] for entry in manifest] == list(range(1, 11))


def test_manifest_entries_have_required_shape_and_files_exist(tmp_path):
    from fixtures import generate_fixtures

    manifest = generate_fixtures.generate(output_dir=str(tmp_path))

    for entry in manifest:
        # Form is a dict and was written to disk.
        assert isinstance(entry["form"], dict)
        assert os.path.exists(entry["form_path"]), f"missing form.json for case {entry['case']}"

        # Expected outcome is well-formed.
        assert entry["expected_status"] in VALID_STATUSES
        # Failing rule is a string for non-approved cases, None for approved ones.
        if entry["expected_status"] == "approved":
            assert entry["expected_failing_rule"] is None
        else:
            assert isinstance(entry["expected_failing_rule"], str)

        # Every referenced document PDF exists, is non-empty, and is well-formed.
        assert entry["documents"], f"case {entry['case']} has no documents"
        for doc in entry["documents"]:
            assert doc["slot"] and doc["doc_type"]
            assert "extracted" in doc and "legible" in doc
            assert os.path.exists(doc["pdf_path"]), f"missing PDF {doc['pdf_path']}"
            assert os.path.getsize(doc["pdf_path"]) > 0


def test_expected_failing_rules_match_section_9_table(tmp_path):
    from fixtures import generate_fixtures

    manifest = {e["case"]: e for e in generate_fixtures.generate(output_dir=str(tmp_path))}

    expected = {
        1: ("approved", None),
        2: ("pending", "MANDATORY_DOCS_PRESENT"),
        3: ("pending", "DOC_WRONG_ATTACHED"),
        4: ("pending", "DOC_LEGIBLE"),
        5: ("rejected", "PAN_FORMAT"),
        6: ("rejected", "GST_PAN_LINK"),
        7: ("pending", "BANK_HOLDER_MATCH"),
        8: ("approved", None),
        9: ("rejected", "PAN_REUSE_NEW_BANK"),
        10: ("rejected", "DUP_BANK_ACCT"),
    }
    for case, (status, rule) in expected.items():
        assert manifest[case]["expected_status"] == status
        assert manifest[case]["expected_failing_rule"] == rule


def test_illegible_case_marks_pan_unreadable(tmp_path):
    from fixtures import generate_fixtures

    manifest = {e["case"]: e for e in generate_fixtures.generate(output_dir=str(tmp_path))}

    pan_docs = [d for d in manifest[4]["documents"] if d["slot"] == "pan"]
    assert pan_docs and pan_docs[0]["legible"] is False
    # Extraction yields nulls for an unreadable scan (no guessing).
    assert pan_docs[0]["extracted"]["pan"] is None


def test_duplicate_cases_align_with_seed_constants(tmp_path):
    from fixtures import generate_fixtures
    from database.seed import SEED_ACCT_A, SEED_PAN_X

    manifest = {e["case"]: e for e in generate_fixtures.generate(output_dir=str(tmp_path))}

    # Case 9 reuses the seeded PAN X with a *different* bank account.
    case9 = manifest[9]["form"]
    assert case9["pan"] == SEED_PAN_X
    assert case9["bank"]["account_number"] != SEED_ACCT_A

    # Case 10 reuses the seeded account A with a *different* PAN.
    case10 = manifest[10]["form"]
    assert case10["bank"]["account_number"] == SEED_ACCT_A
    assert case10["pan"] != SEED_PAN_X


# --- Seed data ----------------------------------------------------------------
@pytest.fixture()
def seed_module(tmp_path, monkeypatch):
    """Reload config + db + seed against an isolated temp DB and init the schema."""
    db_file = tmp_path / "seed_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import database.seed as seed
    importlib.reload(seed)

    db_module.init_db()
    return seed, db_module


def test_seed_inserts_expected_prior_vendors(seed_module):
    seed, db_module = seed_module

    ids = seed.seed()
    assert set(ids) == {seed.SEED_VENDOR_9_ID, seed.SEED_VENDOR_10_ID}

    rows = db_module.query("SELECT * FROM vendors")
    assert len(rows) == 2

    # Case 9 prior vendor: PAN X + account A.
    v9 = db_module.get_vendor(seed.SEED_VENDOR_9_ID)
    assert v9 is not None
    assert v9["pan"] == seed.SEED_PAN_X
    assert v9["bank_account"] == seed.SEED_ACCT_A

    # Case 10 prior vendor: a different PAN, also pointing at account A.
    v10 = db_module.get_vendor(seed.SEED_VENDOR_10_ID)
    assert v10 is not None
    assert v10["pan"] != seed.SEED_PAN_X
    assert v10["bank_account"] == seed.SEED_ACCT_A


def test_seed_is_idempotent(seed_module):
    seed, db_module = seed_module

    seed.seed()
    seed.seed()  # second run must not create duplicates

    rows = db_module.query("SELECT * FROM vendors")
    assert len(rows) == 2
