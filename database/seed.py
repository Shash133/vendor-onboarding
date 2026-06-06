"""database/seed.py — seed prior vendors for the duplicate/fraud edge cases.

Implementation.md §1 + §9: edge cases 9 and 10 require *prior* vendors to already
exist in the ``vendors`` table so the duplicate / reuse rules can fire when a new
submission is processed:

    Case 9  · Reused PAN, new bank  → a prior vendor with **PAN X** and **bank
              account A**. A new submission reusing PAN X but with a *different*
              bank account trips ``PAN_REUSE_NEW_BANK`` (reject, risk +60).
    Case 10 · Shared bank account   → a *different* prior vendor that also points
              at **bank account A**. A new submission (any other vendor) reusing
              account A trips ``DUP_BANK_ACCT`` (reject, risk +60).

The seeded identifiers (**PAN X**, **account A**) are exported as module-level
constants so the fixtures generator and the tests can build submissions that line
up with the seed data — everything stays consistent from one source of truth.

Idempotent: seeding uses fixed vendor ids and inserts-if-absent / updates-in-place,
so ``python -m database.seed`` can be run repeatedly without creating duplicates.
"""

from __future__ import annotations

from database import db
from services.rules.gst import _gstin_checksum


def _valid_gst(pan: str, state: str = "27", entity: str = "1") -> str:
    """Build a format- and checksum-valid 15-char GSTIN derived from ``pan``.

    Layout: ``<state:2><pan:10><entity:1>Z<checksum:1>``. Uses the same checksum
    routine as the GST validation rule so the value is internally consistent.
    """
    body = f"{state}{pan}{entity}Z"  # first 14 chars
    return body + _gstin_checksum(body)


# --- Seeded identifiers (the single source of truth shared with fixtures) -----
# PAN X + account A are the identifiers the duplicate/reuse rules key off.
SEED_PAN_X = "AAACA9999C"          # prior vendor's PAN (case 9 reuses this)
SEED_ACCT_A = "555566667777"       # prior vendor's bank account (cases 9 & 10)
SEED_IFSC = "HDFC0009999"
SEED_GST_X = _valid_gst(SEED_PAN_X)

# A second, *different* prior vendor that also holds account A so case 10's
# "shared bank account across two vendors" lookup has a distinct counterpart.
SEED_VENDOR_10_PAN = "AADCD8888C"
SEED_VENDOR_10_GST = _valid_gst(SEED_VENDOR_10_PAN)

# Fixed vendor ids keep seeding idempotent (insert-if-absent / update-in-place).
SEED_VENDOR_9_ID = "seed-vendor-9-pan-x-acct-a"
SEED_VENDOR_10_ID = "seed-vendor-10-shared-acct-a"

SEED_VENDORS = [
    {
        "vendor_id": SEED_VENDOR_9_ID,
        "legal_name": "Established Supplies Private Limited",
        "pan": SEED_PAN_X,
        "gst": SEED_GST_X,
        "bank_account": SEED_ACCT_A,
        "ifsc": SEED_IFSC,
    },
    {
        "vendor_id": SEED_VENDOR_10_ID,
        "legal_name": "Partner Trading Private Limited",
        "pan": SEED_VENDOR_10_PAN,
        "gst": SEED_VENDOR_10_GST,
        "bank_account": SEED_ACCT_A,  # shared with the vendor above (case 10)
        "ifsc": SEED_IFSC,
    },
]


def seed() -> list[str]:
    """Seed the prior vendors needed for cases 9 & 10; return their vendor ids.

    Ensures the schema exists, then for each seed vendor inserts it when absent
    or updates it in place when already present. Safe to run repeatedly.
    """
    db.init_db()
    ids: list[str] = []
    for v in SEED_VENDORS:
        if db.get_vendor(v["vendor_id"]) is None:
            db.insert_vendor(
                v["vendor_id"],
                v["legal_name"],
                v["pan"],
                v["gst"],
                v["bank_account"],
                v["ifsc"],
            )
        else:
            db.update_vendor(
                v["vendor_id"],
                v["legal_name"],
                v["pan"],
                v["gst"],
                v["bank_account"],
                v["ifsc"],
            )
        ids.append(v["vendor_id"])
    return ids


def main() -> None:
    """CLI entry point: ``python -m database.seed``."""
    ids = seed()
    print(f"Seeded {len(ids)} prior vendor(s): {', '.join(ids)}")
    print(f"  PAN X    = {SEED_PAN_X}")
    print(f"  Account A = {SEED_ACCT_A}")


if __name__ == "__main__":
    main()
