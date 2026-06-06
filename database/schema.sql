-- database/schema.sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS vendors (
  vendor_id    TEXT PRIMARY KEY,
  legal_name   TEXT NOT NULL,
  pan          TEXT,
  gst          TEXT,
  bank_account TEXT,
  ifsc         TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vendors_pan  ON vendors(pan);
CREATE INDEX IF NOT EXISTS idx_vendors_gst  ON vendors(gst);
CREATE INDEX IF NOT EXISTS idx_vendors_acct ON vendors(bank_account);

CREATE TABLE IF NOT EXISTS submissions (
  submission_id TEXT PRIMARY KEY,
  vendor_id     TEXT REFERENCES vendors(vendor_id),
  form_json     TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'received'
                CHECK (status IN ('received','processing','decided')),
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  document_id    TEXT PRIMARY KEY,
  submission_id  TEXT NOT NULL REFERENCES submissions(submission_id),
  slot           TEXT,                      -- which form slot it was uploaded for
  file_path      TEXT NOT NULL,
  doc_type       TEXT,                      -- Agent 1 output
  classify_conf  REAL,
  extracted_json TEXT,                       -- Agent 2 output
  legible        INTEGER NOT NULL DEFAULT 1, -- 0/1
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_results (
  result_id     TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
  rule_id       TEXT NOT NULL,
  category      TEXT NOT NULL,
  severity      TEXT NOT NULL CHECK (severity IN ('warning','pending','reject')),
  outcome       TEXT NOT NULL CHECK (outcome  IN ('pass','warn','fail')),
  reason        TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vr_submission ON validation_results(submission_id);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id        TEXT PRIMARY KEY,
  submission_id      TEXT NOT NULL UNIQUE REFERENCES submissions(submission_id),
  completeness_score REAL NOT NULL,
  consistency_score  REAL NOT NULL,
  compliance_score   REAL NOT NULL,
  fraud_risk_score   REAL NOT NULL,
  final_status       TEXT NOT NULL CHECK (final_status IN ('approved','pending','rejected')),
  explanation_json   TEXT,
  overridden         INTEGER NOT NULL DEFAULT 0,
  override_note      TEXT,
  created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS communications (
  comm_id              TEXT PRIMARY KEY,
  decision_id          TEXT NOT NULL REFERENCES decisions(decision_id),
  channel              TEXT NOT NULL DEFAULT 'email',
  subject              TEXT,
  body                 TEXT,
  requested_items_json TEXT,
  created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  run_id         TEXT PRIMARY KEY,
  submission_id  TEXT NOT NULL REFERENCES submissions(submission_id),
  stage          TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN ('started','ok','error')),
  duration_ms    INTEGER,
  output_summary TEXT,
  started_at     TEXT NOT NULL,
  finished_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_wr_submission ON workflow_runs(submission_id);

CREATE TABLE IF NOT EXISTS audit_logs (
  log_id        TEXT PRIMARY KEY,
  submission_id TEXT NOT NULL REFERENCES submissions(submission_id),
  actor         TEXT NOT NULL,             -- 'system' | 'agent:<name>' | 'reviewer'
  action        TEXT NOT NULL,
  payload_json  TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_submission ON audit_logs(submission_id);
