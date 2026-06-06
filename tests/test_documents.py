"""Tests for the document upload API route (Task 8, Implementation.md §3).

Exercises ``POST /documents/upload`` against an isolated temp DB *and* a temp
UPLOAD_DIR. The monkeypatch + importlib.reload pattern (mirroring
test_submissions.py) points DB_PATH and UPLOAD_DIR at per-test locations before
the app, db, storage, and route modules load, so the suite never touches the
real database or uploads directory.
"""

import importlib
import json
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Build a TestClient bound to a fresh temp DB + temp UPLOAD_DIR."""
    db_file = tmp_path / "test_app.db"
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    # Reload config first so the patched env is picked up, then every module
    # that captured a config value at import time (db, storage), then the audit
    # service, route modules, and finally the app so it binds to the reloaded db.
    import backend.config as config
    importlib.reload(config)
    import database.db as db_module
    importlib.reload(db_module)
    import services.storage as storage
    importlib.reload(storage)
    import services.audit_service as audit_service
    importlib.reload(audit_service)
    import backend.routes.submissions as submissions
    importlib.reload(submissions)
    import backend.routes.documents as documents
    importlib.reload(documents)
    import backend.main as main
    importlib.reload(main)

    db_module.init_db()

    with TestClient(main.app) as test_client:
        yield test_client, db_module


VALID_FORM = {
    "legal_name": "Acme Technologies Private Limited",
    "pan": "ABCDE1234F",
    "gst": "27ABCDE1234F1Z5",
    "address": "1 Industrial Estate, Pune",
    "contact_email": "ops@acme.example",
    "contact_phone": "+919999999999",
    "vendor_type": "company",
    "bank": {
        "account_number": "123456789012",
        "ifsc": "HDFC0001234",
        "account_holder": "Acme Technologies Private Limited",
    },
}


def _create_submission(test_client) -> str:
    return test_client.post("/submissions", json=VALID_FORM).json()["submission_id"]


def test_documents_route_is_registered(client):
    test_client, _ = client
    paths = {route.path for route in test_client.app.routes}
    assert "/documents/upload" in paths


def test_upload_to_existing_submission_persists_row_file_and_audit(client):
    test_client, db_module = client
    submission_id = _create_submission(test_client)

    file_bytes = b"%PDF-1.4 fake pan card bytes"
    resp = test_client.post(
        "/documents/upload",
        data={"submission_id": submission_id, "slot": "pan"},
        files={"file": ("pan_card.pdf", file_bytes, "application/pdf")},
    )

    assert resp.status_code == 200
    body = resp.json()
    document_id = body["document_id"]
    assert document_id
    assert body["slot"] == "pan"
    assert body["file_path"]

    # A documents row exists with the upload-time columns set and agent columns NULL.
    rows = db_module.query(
        "SELECT * FROM documents WHERE document_id = ?", [document_id]
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["submission_id"] == submission_id
    assert row["slot"] == "pan"
    assert row["file_path"] == body["file_path"]
    assert row["legible"] == 1
    assert row["doc_type"] is None
    assert row["classify_conf"] is None
    assert row["extracted_json"] is None
    assert row["created_at"]

    # The file was written to disk with the uploaded bytes.
    assert os.path.exists(body["file_path"])
    with open(body["file_path"], "rb") as fh:
        assert fh.read() == file_bytes

    # A DOCUMENT_UPLOADED audit row was written with the expected payload.
    audit_rows = db_module.query(
        "SELECT * FROM audit_logs WHERE submission_id = ? AND action = ?",
        [submission_id, "DOCUMENT_UPLOADED"],
    )
    assert len(audit_rows) == 1
    payload = json.loads(audit_rows[0]["payload_json"])
    assert payload["document_id"] == document_id
    assert payload["slot"] == "pan"
    assert payload["filename"] == "pan_card.pdf"


def test_upload_to_unknown_submission_returns_404(client):
    test_client, db_module = client

    resp = test_client.post(
        "/documents/upload",
        data={"submission_id": "does-not-exist", "slot": "pan"},
        files={"file": ("pan_card.pdf", b"bytes", "application/pdf")},
    )

    assert resp.status_code == 404

    # No documents row and no audit row should have been written.
    assert db_module.query("SELECT * FROM documents") == []
    assert (
        db_module.query(
            "SELECT * FROM audit_logs WHERE action = ?", ["DOCUMENT_UPLOADED"]
        )
        == []
    )
