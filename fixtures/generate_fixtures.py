"""fixtures/generate_fixtures.py — demo data for the 10 edge cases (Implementation.md §9).

For each of the 10 edge cases in the §9 table this module produces, under
``fixtures/data/case_{n}/``:

    * ``form.json`` — the vendor intake form (SubmissionCreate-shaped).
    * one PDF per uploaded document (rendered with reportlab) carrying the
      relevant text (PAN card, GST certificate, cancelled cheque, bank letter)
      so the downstream extraction agent has something real to read. The
      illegible case (4) is rendered in faint, low-contrast text so it reads as
      unreadable.

It also exposes a programmatic API, :func:`generate` / :func:`get_manifest`,
returning a structured manifest the edge-case tests (Task 19) consume:

    {
      "case": int,
      "title": str,
      "form": dict,                       # the intake form
      "form_path": str,                   # absolute path to form.json
      "documents": [                      # uploaded documents
        {"slot": str, "doc_type": str, "pdf_path": str,
         "extracted": dict, "legible": bool},
      ],
      "expected_status": "approved|pending|rejected",
      "expected_failing_rule": str | None,  # the headline rule from the §9 table
    }

The cases that exercise duplicate / reuse detection (9 & 10) build their forms
from the constants exported by :mod:`database.seed` (PAN X / account A) so they
line up exactly with the seeded prior vendors.

Generation is idempotent (files are overwritten in place) and runnable as
``python -m fixtures.generate_fixtures``.
"""

from __future__ import annotations

import json
import os

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from database.seed import SEED_ACCT_A, SEED_PAN_X
from services.rules.gst import _gstin_checksum

# --- Output location ----------------------------------------------------------
_FIXTURES_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_FIXTURES_DIR, "data")


# --- GSTIN helper -------------------------------------------------------------
def build_valid_gst(pan: str, state: str = "27", entity: str = "1") -> str:
    """Build a format- and checksum-valid 15-char GSTIN derived from ``pan``."""
    body = f"{state}{pan}{entity}Z"  # 14 chars; checksum appended below
    return body + _gstin_checksum(body)


# --- Shared, valid building blocks -------------------------------------------
ADDRESS = "12 Industrial Area, Bengaluru, Karnataka 560001"
EMAIL = "accounts@acme.example"
PHONE = "+91 9876543210"

# Submission-side identifiers for the duplicate cases (paired with seed data).
CASE9_NEW_ACCT = "999988887777"   # PAN X reused, but a *different* bank account
CASE10_PAN = "AAECE7777C"         # a fresh PAN that reuses seeded account A


# --- PDF rendering ------------------------------------------------------------
def _draw_pdf(path: str, title: str, lines: list[str], *, faint: bool = False) -> None:
    """Render a minimal single-page PDF with ``title`` + ``lines`` of text.

    When ``faint`` is True the text is drawn in a light, low-contrast grey at a
    small size so the document reads as an illegible / blurred scan.
    """
    c = canvas.Canvas(path, pagesize=A4)
    _, height = A4
    y = height - 72

    if faint:
        c.setFillColorRGB(0.86, 0.86, 0.86)  # barely-there grey on white
        c.setFont("Helvetica", 7)
    else:
        c.setFillColorRGB(0.0, 0.0, 0.0)
        c.setFont("Helvetica-Bold", 14)

    c.drawString(72, y, title)
    y -= 30
    if not faint:
        c.setFont("Helvetica", 12)
    for line in lines:
        c.drawString(72, y, line)
        y -= 22
    c.showPage()
    c.save()


def _render(kind: str, path: str, fields: dict, *, faint: bool = False) -> None:
    """Render a PDF for a given document ``kind`` from its extracted ``fields``."""
    if kind == "PAN_CARD":
        _draw_pdf(
            path,
            "INCOME TAX DEPARTMENT — GOVT. OF INDIA",
            [
                "Permanent Account Number Card",
                f"PAN: {fields.get('pan')}",
                f"Name: {fields.get('name')}",
            ],
            faint=faint,
        )
    elif kind == "GST_CERTIFICATE":
        _draw_pdf(
            path,
            "GOODS AND SERVICES TAX — CERTIFICATE OF REGISTRATION",
            [
                f"GSTIN: {fields.get('gstin')}",
                f"Legal Name: {fields.get('legal_name')}",
                f"Address: {fields.get('address', ADDRESS)}",
            ],
            faint=faint,
        )
    elif kind == "CANCELLED_CHEQUE":
        _draw_pdf(
            path,
            f"{fields.get('bank_name', 'HDFC BANK')} — CANCELLED CHEQUE",
            [
                f"Account Holder: {fields.get('account_holder')}",
                f"Account Number: {fields.get('account_number')}",
                f"IFSC: {fields.get('ifsc')}",
                "** CANCELLED **",
            ],
            faint=faint,
        )
    elif kind == "BANK_LETTER":
        _draw_pdf(
            path,
            f"{fields.get('bank_name', 'HDFC BANK')} — ACCOUNT CONFIRMATION LETTER",
            [
                f"This confirms the account of: {fields.get('account_holder')}",
                f"Account Number: {fields.get('account_number')}",
                f"IFSC: {fields.get('ifsc')}",
            ],
            faint=faint,
        )
    else:  # pragma: no cover - defensive; all kinds above are covered
        _draw_pdf(path, "DOCUMENT", [json.dumps(fields)], faint=faint)


# --- Form + document builders -------------------------------------------------
def _form(
    legal_name: str,
    pan: str,
    gst: str,
    account_number: str,
    ifsc: str,
    account_holder: str,
    *,
    vendor_type: str = "company",
    contact_email: str = EMAIL,
    contact_phone: str = PHONE,
    address: str = ADDRESS,
) -> dict:
    """Build a SubmissionCreate-shaped intake form dict."""
    return {
        "legal_name": legal_name,
        "pan": pan,
        "gst": gst,
        "address": address,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "vendor_type": vendor_type,
        "bank": {
            "account_number": account_number,
            "ifsc": ifsc,
            "account_holder": account_holder,
        },
    }


def _doc(slot: str, doc_type: str, render_kind: str, extracted: dict, *, legible: bool = True, faint: bool = False) -> dict:
    """Build an internal document spec (resolved to a manifest entry in generate)."""
    return {
        "slot": slot,
        "doc_type": doc_type,
        "render_kind": render_kind,
        "extracted": extracted,
        "legible": legible,
        "faint": faint,
    }


def _pan_doc(pan: str, name: str, *, slot: str = "pan", legible: bool = True, faint: bool = False) -> dict:
    extracted = {"pan": pan, "name": name} if legible else {"pan": None, "name": None}
    return _doc(slot, "PAN_CARD", "PAN_CARD", extracted, legible=legible, faint=faint)


def _gst_doc(gstin: str, legal_name: str, *, slot: str = "gst") -> dict:
    return _doc(
        slot,
        "GST_CERTIFICATE",
        "GST_CERTIFICATE",
        {"gstin": gstin, "legal_name": legal_name, "address": ADDRESS},
    )


def _cheque_doc(account_number: str, ifsc: str, account_holder: str, *, slot: str = "bank") -> dict:
    return _doc(
        slot,
        "CANCELLED_CHEQUE",
        "CANCELLED_CHEQUE",
        {
            "account_number": account_number,
            "ifsc": ifsc,
            "account_holder": account_holder,
            "bank_name": "HDFC BANK",
        },
    )


# --- The 10 edge cases (mapped EXACTLY to Implementation.md §9) ----------------
def _build_cases() -> list[dict]:
    """Return the ordered specs for all 10 edge cases."""
    cases: list[dict] = []

    # 1 · Happy path — consistent form + clean PAN/GST/cheque → Approved.
    name1 = "Acme Technologies Private Limited"
    pan1, acct1, ifsc1 = "AABCA1001C", "100000000001", "HDFC0001234"
    gst1 = build_valid_gst(pan1)
    cases.append({
        "case": 1,
        "title": "Happy path — clean, consistent vendor",
        "form": _form(name1, pan1, gst1, acct1, ifsc1, name1),
        "documents": [
            _pan_doc(pan1, name1),
            _gst_doc(gst1, name1),
            _cheque_doc(acct1, ifsc1, name1),
        ],
        "expected_status": "approved",
        "expected_failing_rule": None,
    })

    # 2 · Missing mandatory document — form + PAN + GST, no bank proof → Pending.
    name2 = "Borealis Trading Private Limited"
    pan2, acct2, ifsc2 = "AABCA1002C", "100000000002", "HDFC0001234"
    gst2 = build_valid_gst(pan2)
    cases.append({
        "case": 2,
        "title": "Missing mandatory document (no bank proof)",
        "form": _form(name2, pan2, gst2, acct2, ifsc2, name2),
        "documents": [
            _pan_doc(pan2, name2),
            _gst_doc(gst2, name2),
        ],
        "expected_status": "pending",
        "expected_failing_rule": "MANDATORY_DOCS_PRESENT",
    })

    # 3 · Wrong document attached — a GST certificate uploaded into the PAN slot.
    name3 = "Cygnus Systems Private Limited"
    pan3, acct3, ifsc3 = "AABCA1003C", "100000000003", "HDFC0001234"
    gst3 = build_valid_gst(pan3)
    cases.append({
        "case": 3,
        "title": "Wrong document attached (GST certificate in the PAN slot)",
        "form": _form(name3, pan3, gst3, acct3, ifsc3, name3),
        "documents": [
            # PAN slot holds a GST certificate (doc_type != slot) → DOC_WRONG_ATTACHED.
            _gst_doc(gst3, name3, slot="pan"),
            _gst_doc(gst3, name3, slot="gst"),
            _cheque_doc(acct3, ifsc3, name3),
        ],
        "expected_status": "pending",
        "expected_failing_rule": "DOC_WRONG_ATTACHED",
    })

    # 4 · Illegible scan — blurred/low-contrast PAN; extraction nulls → Pending.
    name4 = "Delphi Components Private Limited"
    pan4, acct4, ifsc4 = "AABCA1004C", "100000000004", "HDFC0001234"
    gst4 = build_valid_gst(pan4)
    cases.append({
        "case": 4,
        "title": "Illegible PAN scan (low-contrast, unreadable)",
        "form": _form(name4, pan4, gst4, acct4, ifsc4, name4),
        "documents": [
            # legible=False + null extraction; rendered faint so it reads illegible.
            _pan_doc(pan4, name4, legible=False, faint=True),
            _gst_doc(gst4, name4),
            _cheque_doc(acct4, ifsc4, name4),
        ],
        "expected_status": "pending",
        "expected_failing_rule": "DOC_LEGIBLE",
    })

    # 5 · PAN format invalid — pan="ABCD1234XY" → Rejected.
    name5 = "Equinox Logistics Private Limited"
    bad_pan5, acct5, ifsc5 = "ABCD1234XY", "100000000005", "HDFC0001234"
    # GSTIN intentionally not derivable from a malformed PAN; kept clearly invalid.
    gst5 = "27ABCD1234XY1Z5"
    cases.append({
        "case": 5,
        "title": "PAN format invalid",
        "form": _form(name5, bad_pan5, gst5, acct5, ifsc5, name5),
        "documents": [
            _pan_doc(bad_pan5, name5),
            _gst_doc(gst5, name5),
            _cheque_doc(acct5, ifsc5, name5),
        ],
        "expected_status": "rejected",
        "expected_failing_rule": "PAN_FORMAT",
    })

    # 6 · GST not derived from PAN — GSTIN chars 3–12 ≠ PAN → Rejected.
    name6 = "Fulcrum Engineering Private Limited"
    pan6, acct6, ifsc6 = "AABCA1006C", "100000000006", "HDFC0001234"
    gst6 = build_valid_gst("BBBBB2006B")  # valid format, but unrelated to pan6
    cases.append({
        "case": 6,
        "title": "GST not derived from PAN",
        "form": _form(name6, pan6, gst6, acct6, ifsc6, name6),
        "documents": [
            _pan_doc(pan6, name6),
            _gst_doc(gst6, name6),
            _cheque_doc(acct6, ifsc6, name6),
        ],
        "expected_status": "rejected",
        "expected_failing_rule": "GST_PAN_LINK",
    })

    # 7 · Holder mismatch — account_holder "John Doe" vs company name → Pending.
    name7 = "Acme Pvt Ltd"
    pan7, acct7, ifsc7 = "AAACA1007C", "100000000007", "HDFC0001234"
    gst7 = build_valid_gst(pan7)
    cases.append({
        "case": 7,
        "title": "Bank account holder name mismatch",
        # The form's typed account holder differs from the company name.
        "form": _form(name7, pan7, gst7, acct7, ifsc7, "John Doe"),
        "documents": [
            _pan_doc(pan7, name7),
            _gst_doc(gst7, name7),
            # Cheque shows the company name (only the form holder is mismatched),
            # so the mismatch is isolated to BANK_HOLDER_MATCH (not a name reject).
            _cheque_doc(acct7, ifsc7, name7),
        ],
        "expected_status": "pending",
        "expected_failing_rule": "BANK_HOLDER_MATCH",
    })

    # 8 · Fuzzy name variant (legit) — short form vs full PAN name → Approved.
    name8_form = "Acme Tech Pvt Ltd"
    name8_pan = "Acme Technologies Private Limited"
    pan8, acct8, ifsc8 = "AABCA1008C", "100000000008", "HDFC0001234"
    gst8 = build_valid_gst(pan8)
    cases.append({
        "case": 8,
        "title": "Fuzzy name variant (same legal entity)",
        "form": _form(name8_form, pan8, gst8, acct8, ifsc8, name8_form),
        "documents": [
            _pan_doc(pan8, name8_pan),   # full legal name on the PAN card
            _gst_doc(gst8, name8_form),
            _cheque_doc(acct8, ifsc8, name8_form),
        ],
        "expected_status": "approved",
        "expected_failing_rule": None,
    })

    # 9 · Reused PAN, new bank — PAN X (seeded) + a different account → Rejected.
    name9 = "Reused Identity Private Limited"
    gst9 = build_valid_gst(SEED_PAN_X)
    cases.append({
        "case": 9,
        "title": "Reused PAN with changed bank details",
        "form": _form(name9, SEED_PAN_X, gst9, CASE9_NEW_ACCT, "HDFC0001234", name9),
        "documents": [
            _pan_doc(SEED_PAN_X, name9),
            _gst_doc(gst9, name9),
            _cheque_doc(CASE9_NEW_ACCT, "HDFC0001234", name9),
        ],
        "expected_status": "rejected",
        "expected_failing_rule": "PAN_REUSE_NEW_BANK",
    })

    # 10 · Shared bank account — fresh PAN reusing seeded account A → Rejected.
    name10 = "Shared Account Private Limited"
    gst10 = build_valid_gst(CASE10_PAN)
    cases.append({
        "case": 10,
        "title": "Bank account shared with another vendor",
        "form": _form(name10, CASE10_PAN, gst10, SEED_ACCT_A, "HDFC0001234", name10),
        "documents": [
            _pan_doc(CASE10_PAN, name10),
            _gst_doc(gst10, name10),
            _cheque_doc(SEED_ACCT_A, "HDFC0001234", name10),
        ],
        "expected_status": "rejected",
        "expected_failing_rule": "DUP_BANK_ACCT",
    })

    return cases


# --- Public API ---------------------------------------------------------------
def generate(output_dir: str = DATA_DIR) -> list[dict]:
    """Write form JSON + PDFs for every edge case and return the manifest.

    Idempotent: ``case_{n}`` directories and their files are overwritten in
    place on each run. Returns the structured manifest (see module docstring)
    that the edge-case tests consume.
    """
    manifest: list[dict] = []
    for case in _build_cases():
        case_dir = os.path.join(output_dir, f"case_{case['case']}")
        os.makedirs(case_dir, exist_ok=True)

        documents_out: list[dict] = []
        for doc in case["documents"]:
            pdf_path = os.path.join(case_dir, f"{doc['slot']}.pdf")
            _render(doc["render_kind"], pdf_path, doc["extracted"], faint=doc["faint"])
            documents_out.append({
                "slot": doc["slot"],
                "doc_type": doc["doc_type"],
                "pdf_path": pdf_path,
                "extracted": doc["extracted"],
                "legible": doc["legible"],
            })

        form_path = os.path.join(case_dir, "form.json")
        with open(form_path, "w", encoding="utf-8") as fh:
            json.dump(case["form"], fh, indent=2)

        manifest.append({
            "case": case["case"],
            "title": case["title"],
            "form": case["form"],
            "form_path": form_path,
            "documents": documents_out,
            "expected_status": case["expected_status"],
            "expected_failing_rule": case["expected_failing_rule"],
        })

    return manifest


def get_manifest(output_dir: str = DATA_DIR) -> list[dict]:
    """Convenience alias: (re)generate the fixtures and return the manifest."""
    return generate(output_dir)


def main() -> None:
    """CLI entry point: ``python -m fixtures.generate_fixtures``."""
    manifest = generate()
    print(f"Generated fixtures for {len(manifest)} edge case(s) under {DATA_DIR}")
    for entry in manifest:
        n_docs = len(entry["documents"])
        rule = entry["expected_failing_rule"] or "—"
        print(
            f"  case {entry['case']:>2}: {entry['expected_status']:<9} "
            f"({n_docs} doc(s), rule: {rule}) — {entry['title']}"
        )


if __name__ == "__main__":
    main()
