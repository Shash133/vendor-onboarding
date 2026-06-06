"""File storage service (Implementation.md §1).

Saves raw uploaded vendor files under ``uploads/{submission_id}/{document_id}.{ext}``.
``UPLOAD_DIR`` (from ``backend.config``) is the storage base; relative paths are
anchored to the project root the same way ``database/db.py`` resolves ``DB_PATH``,
so the location is stable regardless of the process working directory.

This module is intentionally minimal and parameter-safe: it owns no business
logic, only path construction and byte persistence.
"""

from __future__ import annotations

import os

from backend.config import UPLOAD_DIR

# Project root is the parent of the services/ directory. Used to resolve a
# relative UPLOAD_DIR (the .env default is "./uploads") consistently no matter
# which working directory the process was started from. Mirrors db.py.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_upload_dir() -> str:
    """Return an absolute path for UPLOAD_DIR, anchoring relatives to project root."""
    if os.path.isabs(UPLOAD_DIR):
        return UPLOAD_DIR
    return os.path.normpath(os.path.join(_PROJECT_ROOT, UPLOAD_DIR))


def _ext_from_filename(filename: str) -> str:
    """Derive a lowercase file extension (without dot) from an original filename.

    Returns an empty string when the filename has no usable extension.
    """
    if not filename:
        return ""
    ext = os.path.splitext(filename)[1]  # includes leading dot, or "" if none
    return ext[1:].lower() if ext else ""


def submission_dir(submission_id: str) -> str:
    """Return the absolute directory that holds a submission's uploaded files."""
    return os.path.join(_resolve_upload_dir(), submission_id)


def file_path(submission_id: str, document_id: str, filename: str) -> str:
    """Return the absolute target path for a stored document.

    The basename is ``{document_id}.{ext}`` where ``ext`` derives from the
    original filename; when no extension is present the document id is used
    without a suffix.
    """
    ext = _ext_from_filename(filename)
    name = f"{document_id}.{ext}" if ext else document_id
    return os.path.join(submission_dir(submission_id), name)


def save_file(submission_id: str, document_id: str, file_bytes: bytes, filename: str) -> str:
    """Persist ``file_bytes`` and return the absolute stored file path.

    Creates ``uploads/{submission_id}/`` as needed and writes the bytes to
    ``{document_id}.{ext}``. ``ext`` is derived from ``filename``.
    """
    target = file_path(submission_id, document_id, filename)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(file_bytes)
    return target
