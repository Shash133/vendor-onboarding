"""Submission form page (Implementation.md §7).

Components: text inputs (legal name, PAN, GST, address, contacts, bank) and one
``st.file_uploader`` per document slot (PAN / GST / bank). The "Submit & Run"
action creates the submission, uploads each attached file, triggers the workflow,
and then routes to the live timeline.

API calls (all via :mod:`frontend.api_client`):
``POST /submissions`` → ``POST /documents/upload`` (per file) →
``POST /workflow/run`` (kick off the pipeline) → navigate to the timeline page.

All rendering lives inside :func:`render` so importing the module is side-effect
free for the smoke test.
"""

from __future__ import annotations

import streamlit as st

from frontend import api_client
from frontend.api_client import ApiError

# Document slots offered on the form and the doc_type the backend expects them to
# classify into (kept here only for the uploader labels).
_SLOTS = [
    ("pan", "PAN card"),
    ("gst", "GST certificate"),
    ("bank", "Bank proof (cancelled cheque / bank letter)"),
]

_VENDOR_TYPES = ["company", "proprietor", "partnership"]


def render() -> None:
    """Render the new-submission form and handle the Submit & Run action."""
    st.title("📝 New Vendor Submission")
    st.caption("Fill the form, attach documents, then submit to run the pipeline.")

    with st.form("submission_form", clear_on_submit=False):
        st.subheader("Vendor details")
        legal_name = st.text_input("Legal name")
        col_pan, col_gst = st.columns(2)
        pan = col_pan.text_input("PAN", help="Format: ABCDE1234F")
        gst = col_gst.text_input("GST", help="15-character GSTIN")
        address = st.text_area("Address")
        col_email, col_phone = st.columns(2)
        contact_email = col_email.text_input("Contact email")
        contact_phone = col_phone.text_input("Contact phone")
        vendor_type = st.selectbox("Vendor type", options=_VENDOR_TYPES, index=0)

        st.subheader("Bank details")
        col_acct, col_ifsc = st.columns(2)
        account_number = col_acct.text_input("Account number")
        ifsc = col_ifsc.text_input("IFSC")
        account_holder = st.text_input("Account holder name")

        st.subheader("Documents")
        uploaded: dict[str, object] = {}
        for slot, label in _SLOTS:
            uploaded[slot] = st.file_uploader(
                label, key=f"upload_{slot}", type=None
            )

        submitted = st.form_submit_button("Submit & Run")

    if not submitted:
        return

    form = {
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

    missing = [k for k in ("legal_name", "pan", "gst") if not form[k]]
    if missing:
        st.warning(f"Please fill the required fields: {', '.join(missing)}.")
        return

    _submit_and_run(form, uploaded)


def _submit_and_run(form: dict, uploaded: dict[str, object]) -> None:
    """Create the submission, upload files, start the workflow, go to timeline."""
    try:
        with st.spinner("Creating submission…"):
            created = api_client.create_submission(form)
        submission_id = created["submission_id"]
        st.success(f"Submission created: {submission_id}")

        for slot, file in uploaded.items():
            if file is None:
                continue
            with st.spinner(f"Uploading {slot} document…"):
                api_client.upload_document(submission_id, slot, file)
            st.write(f"✓ Uploaded {slot} document")

        # Trigger the pipeline. The timeline page will stream/poll live progress;
        # we kick it off here so the decision is ready when the user lands there.
        with st.spinner("Starting workflow…"):
            api_client.run_workflow(submission_id)

    except ApiError as exc:
        st.error(exc.message)
        return

    st.session_state["selected_submission_id"] = submission_id
    st.session_state["page"] = "Workflow Timeline"
    st.success("Workflow started — opening the timeline.")
    st.rerun()
