"""Document upload API route (Implementation.md §3).

One thin endpoint that accepts a single uploaded file for a submission + form
slot. Following the project rule "all routes are thin: validate → call service →
map to response model", this module only validates input, calls the storage and
audit services / db accessors, and maps the result to ``DocumentUploadResp``.

- ``POST /documents/upload``  multipart/form-data: ``submission_id``, ``slot``,
  ``file``. Saves the bytes via ``services.storage.save_file``, inserts a
  ``documents`` row (agent-derived columns left NULL), and logs a
  ``DOCUMENT_UPLOADED`` audit event.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from database import db
from models.schemas import DocumentUploadResp
from services import audit_service, storage

router = APIRouter()


@router.post("/documents/upload", response_model=DocumentUploadResp)
async def upload_document(
    submission_id: str = Form(...),
    slot: str = Form(...),
    file: UploadFile = File(...),
) -> DocumentUploadResp:
    """Store one uploaded file for an existing submission + slot.

    Validates the submission exists (404 otherwise), persists the file bytes to
    ``uploads/{submission_id}/{document_id}.{ext}``, inserts a ``documents`` row,
    and writes a ``DOCUMENT_UPLOADED`` audit event with ``{document_id, slot,
    filename}``. The agent-derived columns (``doc_type``/``classify_conf``/
    ``extracted_json``) are populated by later workflow stages, not here.
    """
    submission = db.get_submission(submission_id)
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")

    document_id = db.new_id()
    filename = file.filename or ""
    file_bytes = await file.read()

    file_path = storage.save_file(submission_id, document_id, file_bytes, filename)

    # Insert the documents row with the pre-generated document_id so the stored
    # file path and the row share the same id. Agent-derived columns
    # (doc_type/classify_conf/extracted_json) are left NULL for later stages.
    db.insert_document(submission_id, slot, file_path, document_id=document_id)

    audit_service.log_event(
        submission_id,
        "system",
        "DOCUMENT_UPLOADED",
        {"document_id": document_id, "slot": slot, "filename": filename},
    )

    return DocumentUploadResp(document_id=document_id, slot=slot, file_path=file_path)
