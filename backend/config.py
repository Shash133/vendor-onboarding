"""Application configuration.

Loads environment variables (with defaults matching `.env.example`) and exposes
the deterministic scoring thresholds and fraud-signal weights used by the decision
engine. Values come from Architecture.md Part 7 (Decision Engine) and the
Implementation.md risk-weight notes. Where a value is not explicitly stated, the
simplest value consistent with both documents is chosen and commented.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load environment variables from the project-root ``.env`` before reading them.
# ``config.py`` lives at ``<project_root>/backend/config.py``; the ``.env`` sits
# at ``<project_root>/.env``. We resolve it explicitly (rather than relying on
# the current working directory) so the values load no matter where the app is
# started from. ``override=False`` keeps any real environment variables winning
# over the file, which matters in CI / production.
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

# --- Environment variables (defaults mirror .env.example) ---------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
DB_PATH = os.getenv("DB_PATH", "./database/app.db")
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# --- Fraud signal weights (Architecture.md Part 7) ----------------------------
# Risk score = min(100, sum of triggered signal weights). Higher = riskier.
FRAUD_WEIGHTS = {
    "DUP_BANK_ACCT": 60,        # bank account shared with another vendor
    "PAN_REUSE_NEW_BANK": 60,   # same PAN as existing vendor, different bank details
    "DUP_PAN": 30,              # PAN already exists on another vendor
    "DUP_GST": 25,              # GST already exists on another vendor
    "BANK_HOLDER_MATCH": 25,    # account holder name does not match legal name
    "NAME_HARD_MISMATCH": 40,   # core entity name clearly different
}
FRAUD_SCORE_CAP = 100  # risk score is capped at 100

# --- Decision gate thresholds (Architecture.md Part 7) ------------------------
# Fraud risk gates.
FRAUD_REJECT_THRESHOLD = 60   # fraud_risk >= 60 => rejected
FRAUD_PENDING_THRESHOLD = 30  # fraud_risk in [30, 59] => pending

# Sub-score floors required for an approval (warnings are still allowed).
COMPLETENESS_FLOOR = 100  # completeness must be 100 to approve
CONSISTENCY_FLOOR = 80    # consistency must be >= 80 to approve
COMPLIANCE_FLOOR = 100    # compliance must be 100 to approve

# --- Scoring helper constants -------------------------------------------------
# Consistency penalty per fuzzy name/cross-doc mismatch (Architecture.md Part 7:
# "fuzzy < 0.85 = -20"). Start consistency at 100 and subtract this per mismatch.
CONSISTENCY_MISMATCH_PENALTY = 20

# Fuzzy name-match threshold used by the consistency agent / rules.
NAME_FUZZY_THRESHOLD = 0.85

# Minimum acceptable document classification confidence (rule 27 / Agent 1).
CLASSIFY_CONFIDENCE_THRESHOLD = 0.6
