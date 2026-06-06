"""SQLite connection and access layer.

Implements the database helpers described in Implementation.md §2:
`get_conn()`, `init_db()`, `execute()`, `query()`, `query_one()`, plus thin
parameterized accessors used by the workflow engine. No business logic lives
here — callers own all decision making.

Conventions (Implementation.md §2):
- Foreign keys are enabled on every connection.
- All ``*_id`` columns are TEXT UUID4.
- All timestamps are ISO-8601 TEXT in UTC.
- Only parameterized queries are used; values are never string-interpolated.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from backend.config import DB_PATH

# schema.sql lives alongside this module.
_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

# Project root is the parent of the database/ directory. Used to resolve a
# relative DB_PATH (the .env default is "./database/app.db") consistently no
# matter which working directory the process was started from.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_db_path() -> str:
    """Return an absolute path for DB_PATH, anchoring relatives to the project root."""
    if os.path.isabs(DB_PATH):
        return DB_PATH
    return os.path.normpath(os.path.join(_PROJECT_ROOT, DB_PATH))


# --- Time / id helpers --------------------------------------------------------
def utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string (seconds precision, ``Z`` suffix)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    """Generate a fresh UUID4 hex-less string id (TEXT primary keys)."""
    return str(uuid.uuid4())


# --- Connection / init --------------------------------------------------------
def get_conn() -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys enabled and Row factory set."""
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Create the database directory if needed and execute schema.sql.

    Idempotent: schema.sql uses ``IF NOT EXISTS`` for every table and index.
    """
    db_path = _resolve_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        schema_sql = fh.read()

    conn = get_conn()
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


# --- Core query helpers -------------------------------------------------------
def execute(sql: str, params=None):
    """Run a write statement, commit, and return ``lastrowid``.

    For INSERTs of rows with explicit TEXT UUID primary keys, callers generate
    and pass the id themselves; ``lastrowid`` is returned for completeness.
    """
    conn = get_conn()
    try:
        cur = conn.execute(sql, params or [])
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def query(sql: str, params=None) -> list[dict]:
    """Run a read statement and return rows as a list of dicts."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params or [])
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def query_one(sql: str, params=None) -> dict | None:
    """Run a read statement and return the first row as a dict, or None."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params or [])
        row = cur.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


# --- Thin accessors used by the workflow engine -------------------------------
def get_submission(submission_id: str) -> dict | None:
    """Return the submission row, or None."""
    return query_one(
        "SELECT * FROM submissions WHERE submission_id = ?",
        [submission_id],
    )


# --- Vendor identity accessors (duplicate / reuse detection) ------------------
def get_vendor(vendor_id: str) -> dict | None:
    """Return a vendors row by id, or None."""
    return query_one("SELECT * FROM vendors WHERE vendor_id = ?", [vendor_id])


def insert_vendor(
    vendor_id: str,
    legal_name: str,
    pan: str | None,
    gst: str | None,
    bank_account: str | None,
    ifsc: str | None,
) -> str:
    """Insert a ``vendors`` row (parameterized) and return its vendor_id.

    The vendors table is the stable identity store the duplicate rules query
    against, so FUTURE submissions can detect reused PAN/GST/bank accounts.
    """
    execute(
        "INSERT INTO vendors("
        "vendor_id, legal_name, pan, gst, bank_account, ifsc, created_at"
        ") VALUES (?,?,?,?,?,?,?)",
        [vendor_id, legal_name, pan, gst, bank_account, ifsc, utcnow_iso()],
    )
    return vendor_id


def update_vendor(
    vendor_id: str,
    legal_name: str,
    pan: str | None,
    gst: str | None,
    bank_account: str | None,
    ifsc: str | None,
) -> None:
    """Update an existing ``vendors`` row in place (idempotent re-runs)."""
    execute(
        "UPDATE vendors SET legal_name = ?, pan = ?, gst = ?, "
        "bank_account = ?, ifsc = ? WHERE vendor_id = ?",
        [legal_name, pan, gst, bank_account, ifsc, vendor_id],
    )


def set_submission_vendor(submission_id: str, vendor_id: str) -> None:
    """Link a submission to its resolved vendor identity (parameterized)."""
    execute(
        "UPDATE submissions SET vendor_id = ? WHERE submission_id = ?",
        [vendor_id, submission_id],
    )


def get_documents(submission_id: str) -> list[dict]:
    """Return all document rows for a submission, oldest first."""
    return query(
        "SELECT * FROM documents WHERE submission_id = ? ORDER BY created_at ASC",
        [submission_id],
    )


def insert_document(
    submission_id: str,
    slot: str | None,
    file_path: str,
    *,
    document_id: str | None = None,
    legible: int = 1,
) -> str:
    """Insert a ``documents`` row and return its document_id.

    Only the columns known at upload time are written: ``document_id``,
    ``submission_id``, ``slot``, ``file_path``, ``legible`` (default 1) and
    ``created_at``. The agent-derived columns (``doc_type``, ``classify_conf``,
    ``extracted_json``) are left NULL — they are filled in by later agent stages.

    ``document_id`` may be supplied by the caller (the upload route generates it
    first so the stored file path and the row share the same id); when omitted a
    fresh id is generated.
    """
    document_id = document_id or new_id()
    execute(
        "INSERT INTO documents("
        "document_id, submission_id, slot, file_path, legible, created_at"
        ") VALUES (?,?,?,?,?,?)",
        [document_id, submission_id, slot, file_path, legible, utcnow_iso()],
    )
    return document_id


def update_document_classification(
    document_id: str,
    doc_type: str | None,
    classify_conf: float | None,
    legible: int = 1,
) -> None:
    """Persist Agent 1 output onto a documents row (doc_type/classify_conf/legible)."""
    execute(
        "UPDATE documents SET doc_type = ?, classify_conf = ?, legible = ? WHERE document_id = ?",
        [doc_type, classify_conf, legible, document_id],
    )


def update_document_extraction(document_id: str, extracted_json: str | None) -> None:
    """Persist Agent 2 output (the raw extraction JSON) onto a documents row."""
    execute(
        "UPDATE documents SET extracted_json = ? WHERE document_id = ?",
        [extracted_json, document_id],
    )


def list_submissions() -> list[dict]:
    """Return submissions joined with their decision (if any) for the dashboard.

    LEFT JOINs ``decisions`` so submissions without a decision still appear, with
    ``decision_status``/``fraud_risk_score`` reported as NULL. Newest first.
    """
    return query(
        "SELECT s.submission_id, s.form_json, s.status, s.created_at, "
        "d.final_status AS decision_status, d.fraud_risk_score AS fraud_risk_score "
        "FROM submissions s "
        "LEFT JOIN decisions d ON d.submission_id = s.submission_id "
        "ORDER BY s.created_at DESC"
    )


def count_submissions() -> int:
    """Return the total number of submissions (dashboard "Total" metric)."""
    row = query_one("SELECT COUNT(*) AS n FROM submissions")
    return int(row["n"]) if row else 0


def count_decisions_by_status() -> dict:
    """Return decision counts keyed by ``final_status`` (approved/pending/rejected).

    Statuses with no decisions are omitted from the raw query result; the caller
    is responsible for defaulting missing keys to 0. Read-only, parameter-free.
    """
    rows = query(
        "SELECT final_status, COUNT(*) AS n FROM decisions GROUP BY final_status"
    )
    return {row["final_status"]: int(row["n"]) for row in rows}


def recent_activity(limit: int = 10) -> list[dict]:
    """Return the most recent submissions (newest first) for the dashboard feed.

    Each entry carries the submission id, status, decision status (if any) and
    created_at timestamp. ``limit`` is passed as a bound parameter.
    """
    return query(
        "SELECT s.submission_id, s.form_json, s.status, s.created_at, "
        "d.final_status AS decision_status "
        "FROM submissions s "
        "LEFT JOIN decisions d ON d.submission_id = s.submission_id "
        "ORDER BY s.created_at DESC LIMIT ?",
        [limit],
    )


def get_validation_results(submission_id: str) -> list[dict]:
    """Return a submission's validation_results rows, oldest first."""
    return query(
        "SELECT * FROM validation_results WHERE submission_id = ? ORDER BY created_at ASC",
        [submission_id],
    )


def get_decision(submission_id: str) -> dict | None:
    """Return the submission's decision row (1→1), or None."""
    return query_one(
        "SELECT * FROM decisions WHERE submission_id = ?",
        [submission_id],
    )


def get_communications(submission_id: str) -> list[dict]:
    """Return communications for a submission via its decision, oldest first."""
    return query(
        "SELECT c.* FROM communications c "
        "JOIN decisions d ON d.decision_id = c.decision_id "
        "WHERE d.submission_id = ? ORDER BY c.created_at ASC",
        [submission_id],
    )


def insert_decision(submission_id: str, scores: dict, status: str, explanation) -> str:
    """Insert a decisions row and return the generated decision_id.

    ``scores`` must provide the four sub-scores; ``explanation`` is serialized
    to JSON (may be a dict or None).
    """
    decision_id = new_id()
    execute(
        "INSERT INTO decisions("
        "decision_id, submission_id, completeness_score, consistency_score, "
        "compliance_score, fraud_risk_score, final_status, explanation_json, created_at"
        ") VALUES (?,?,?,?,?,?,?,?,?)",
        [
            decision_id,
            submission_id,
            scores["completeness_score"],
            scores["consistency_score"],
            scores["compliance_score"],
            scores["fraud_risk_score"],
            status,
            json.dumps(explanation) if explanation is not None else None,
            utcnow_iso(),
        ],
    )
    return decision_id


def clear_submission_outputs(submission_id: str) -> None:
    """Delete derived outputs for a submission so the workflow can be re-run.

    Removes (in FK-safe order) the submission's communications, its decision row
    (``decisions.submission_id`` is UNIQUE — 1:1), and its ``validation_results``.
    Append-only tables (``audit_logs``, ``workflow_runs``) are intentionally left
    untouched so the historical trail is preserved across re-runs.

    NOTE: a re-run regenerates the decision from scratch, so any reviewer override
    previously applied via :func:`override_decision` is discarded.
    """
    # Communications hang off the decision (FK decision_id) — delete them first.
    execute(
        "DELETE FROM communications WHERE decision_id IN ("
        "SELECT decision_id FROM decisions WHERE submission_id = ?)",
        [submission_id],
    )
    execute("DELETE FROM decisions WHERE submission_id = ?", [submission_id])
    execute("DELETE FROM validation_results WHERE submission_id = ?", [submission_id])


def override_decision(submission_id: str, new_status: str, note: str | None) -> None:
    """Apply a reviewer override to a decisions row (parameterized).

    Sets ``overridden = 1``, records the ``override_note``, and replaces the
    ``final_status`` with the reviewer's ``new_status``. The ``decisions`` table
    is mutable (only ``audit_logs`` is append-only), so an UPDATE here is allowed;
    the override itself is recorded immutably in ``audit_logs`` by the caller.
    """
    execute(
        "UPDATE decisions SET overridden = 1, override_note = ?, final_status = ? "
        "WHERE submission_id = ?",
        [note, new_status, submission_id],
    )


def insert_communication(decision_id: str, comm: dict) -> str:
    """Insert a communications row and return the generated comm_id."""
    comm_id = new_id()
    execute(
        "INSERT INTO communications("
        "comm_id, decision_id, channel, subject, body, requested_items_json, created_at"
        ") VALUES (?,?,?,?,?,?,?)",
        [
            comm_id,
            decision_id,
            comm.get("channel", "email"),
            comm.get("subject"),
            comm.get("body"),
            json.dumps(comm.get("requested_items", [])),
            utcnow_iso(),
        ],
    )
    return comm_id


def set_submission_status(submission_id: str, status: str) -> None:
    """Update a submission's status (parameterized)."""
    execute(
        "UPDATE submissions SET status = ? WHERE submission_id = ?",
        [status, submission_id],
    )


def insert_workflow_run_start(submission_id: str, stage: str) -> str:
    """Insert a ``workflow_runs`` row at stage start (status ``started``).

    Returns the generated run_id so the caller can update the same row when the
    stage finishes (see :func:`finish_workflow_run`). ``duration_ms``,
    ``output_summary`` and ``finished_at`` are left NULL until the stage ends.
    """
    run_id = new_id()
    execute(
        "INSERT INTO workflow_runs("
        "run_id, submission_id, stage, status, started_at"
        ") VALUES (?,?,?,?,?)",
        [run_id, submission_id, stage, "started", utcnow_iso()],
    )
    return run_id


def finish_workflow_run(
    run_id: str,
    status: str,
    duration_ms: int,
    output_summary: str | None,
) -> None:
    """Update a ``workflow_runs`` row when a stage finishes (``ok``/``error``).

    Records the terminal ``status`` (ok|error), the measured ``duration_ms``, a
    short ``output_summary``, and the ``finished_at`` timestamp.
    """
    execute(
        "UPDATE workflow_runs SET status = ?, duration_ms = ?, "
        "output_summary = ?, finished_at = ? WHERE run_id = ?",
        [status, duration_ms, output_summary, utcnow_iso(), run_id],
    )


def get_workflow_runs(submission_id: str) -> list[dict]:
    """Return a submission's workflow_runs rows, oldest first (read-only)."""
    return query(
        "SELECT * FROM workflow_runs WHERE submission_id = ? ORDER BY started_at ASC",
        [submission_id],
    )
