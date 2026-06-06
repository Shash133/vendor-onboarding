"""Tests for the file storage service (Task 6, Implementation.md §1)."""

import importlib
import os

import pytest


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    """Load storage.py against a temporary UPLOAD_DIR."""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setenv("UPLOAD_DIR", str(upload_dir))

    # Reload config + storage so the patched UPLOAD_DIR is picked up.
    import backend.config as config
    importlib.reload(config)
    import services.storage as storage_module
    importlib.reload(storage_module)

    return storage_module


def test_save_file_writes_to_expected_path(storage):
    path = storage.save_file("sub-1", "doc-1", b"hello", "scan.PDF")

    # Path layout: {UPLOAD_DIR}/{submission_id}/{document_id}.{ext} (ext lowercased).
    assert os.path.basename(path) == "doc-1.pdf"
    assert os.path.basename(os.path.dirname(path)) == "sub-1"
    assert os.path.isfile(path)


def test_save_file_returns_path_and_persists_bytes(storage):
    path = storage.save_file("sub-2", "doc-2", b"\x00\x01binary", "cheque.png")

    with open(path, "rb") as fh:
        assert fh.read() == b"\x00\x01binary"


def test_save_file_creates_submission_directory(storage):
    assert not os.path.exists(storage.submission_dir("sub-3"))
    storage.save_file("sub-3", "doc-3", b"x", "a.jpg")
    assert os.path.isdir(storage.submission_dir("sub-3"))


def test_file_path_handles_missing_extension(storage):
    path = storage.file_path("sub-4", "doc-4", "no_extension")
    assert os.path.basename(path) == "doc-4"


def test_save_file_uses_absolute_path_under_upload_dir(storage):
    path = storage.save_file("sub-5", "doc-5", b"y", "x.txt")
    assert os.path.isabs(path)
    assert os.path.normpath(storage._resolve_upload_dir()) in os.path.normpath(path)
