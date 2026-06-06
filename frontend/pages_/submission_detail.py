"""Submission detail page (Implementation.md §7).

Components: vendor panel, document viewer, validation-results table (colour-coded
by severity), risk panel, decision banner, and an editable email box. Reviewer
actions: override the decision and re-run the pipeline.

API calls (via :mod:`frontend.api_client`): ``GET /submission/{id}`` for the full
detail, ``POST /decision/{id}/override`` for the override, and
``POST /workflow/run`` to re-run the pipeline.

Colour map for the results table (Implementation.md §7):
``reject`` = red, ``pending`` = yellow, ``warning`` = gray, ``pass`` = green.

All rendering lives in :func:`render`; importing the module has no side effects.
"""

from __future__ import annotations

import streamlit as st

from frontend import api_client
from frontend.api_client import ApiError

# Severity → colour (hex) used to tint the validation-results rows.
_SEVERITY_COLORS = {
    "reject": "#e74c3c",   # red
    "pending": "#f1c40f",  # yellow
    "warning": "#95a5a6",  # gray
}
# A passing outcome is always green regardless of its severity classification.
_PASS_COLOR = "#2ecc71"  # green

_OVERRIDE_STATUSES = ["approved", "pending", "rejected"]


def render() -> None:
    """Render the detail page for the currently selected submission."""
    st.title("🔎 Submission Detail")

    submission_id = st.session_state.get("selected_submission_id")
    if not submission_id:
        st.info("Select a submission from the dashboard first.")
        return

    try:
        detail = api_client.get_submission(submission_id)
    except ApiError as exc:
        st.error(exc.message)
        return

    st.caption(f"Submission `{submission_id}` · status: {detail.get('status')}")

    _render_actions(submission_id)
    _render_vendor_panel(detail.get("form", {}))
    _render_decision_banner(detail.get("decision"))
    _render_documents(detail.get("documents", []))
    _render_validation_results(detail.get("validation_results", []))
    _render_risk_panel(detail.get("decision"))
    _render_communications(detail.get("communications", []))
    _render_override(submission_id, detail.get("decision"))


def _render_actions(submission_id: str) -> None:
    """Top action bar: re-run the pipeline."""
    col_rerun, _ = st.columns([1, 4])
    if col_rerun.button("🔁 Re-run pipeline"):
        try:
            with st.spinner("Re-running the workflow…"):
                api_client.run_workflow(submission_id)
            st.success("Pipeline re-run complete.")
            st.rerun()
        except ApiError as exc:
            st.error(exc.message)


def _render_vendor_panel(form: dict) -> None:
    """Show the submitted vendor fields."""
    st.subheader("Vendor")
    if not form:
        st.caption("No form data.")
        return
    col_a, col_b = st.columns(2)
    col_a.write(f"**Legal name:** {form.get('legal_name', '—')}")
    col_a.write(f"**PAN:** {form.get('pan', '—')}")
    col_a.write(f"**GST:** {form.get('gst', '—')}")
    col_a.write(f"**Vendor type:** {form.get('vendor_type', '—')}")
    col_b.write(f"**Email:** {form.get('contact_email', '—')}")
    col_b.write(f"**Phone:** {form.get('contact_phone', '—')}")
    bank = form.get("bank", {}) or {}
    col_b.write(f"**Bank A/C:** {bank.get('account_number', '—')}")
    col_b.write(f"**IFSC:** {bank.get('ifsc', '—')}")
    col_b.write(f"**Holder:** {bank.get('account_holder', '—')}")
    if form.get("address"):
        st.write(f"**Address:** {form['address']}")


def _render_decision_banner(decision: dict | None) -> None:
    """Render the decision status banner + 4 sub-scores."""
    st.subheader("Decision")
    if not decision:
        st.info("No decision yet — run the pipeline.")
        return

    status = decision.get("final_status")
    banner = {
        "approved": st.success,
        "pending": st.warning,
        "rejected": st.error,
    }.get(status, st.info)
    overridden = decision.get("overridden")
    label = f"Decision: {str(status).upper()}"
    if overridden:
        label += " (overridden)"
    banner(label)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Completeness", _fmt(decision.get("completeness_score")))
    col2.metric("Consistency", _fmt(decision.get("consistency_score")))
    col3.metric("Compliance", _fmt(decision.get("compliance_score")))
    col4.metric("Fraud risk", _fmt(decision.get("fraud_risk_score")))

    explanation = decision.get("explanation")
    if explanation:
        with st.expander("Explanation", expanded=True):
            if isinstance(explanation, dict):
                if explanation.get("summary"):
                    st.write(explanation["summary"])
                if explanation.get("key_drivers"):
                    st.write("**Key drivers:**")
                    for d in explanation["key_drivers"]:
                        st.write(f"• {d}")
                if explanation.get("what_would_change_it"):
                    st.write("**What would change it:**")
                    for d in explanation["what_would_change_it"]:
                        st.write(f"• {d}")
            else:
                st.write(explanation)


def _render_documents(documents: list[dict]) -> None:
    """Document viewer: type, slot, confidence, legibility, extracted fields."""
    st.subheader("Documents")
    if not documents:
        st.caption("No documents uploaded.")
        return
    for doc in documents:
        title = doc.get("doc_type") or doc.get("slot") or "document"
        with st.expander(f"{title} · {doc.get('file_path', '')}"):
            st.write(f"**Slot:** {doc.get('slot', '—')}")
            st.write(f"**Detected type:** {doc.get('doc_type', '—')}")
            st.write(f"**Confidence:** {_fmt(doc.get('classify_conf'))}")
            st.write(f"**Legible:** {'yes' if doc.get('legible', 1) else 'no'}")
            extracted = doc.get("extracted_json")
            if extracted:
                st.write("**Extracted fields:**")
                st.json(extracted)


def _render_validation_results(results: list[dict]) -> None:
    """Colour-coded validation-results table (Implementation.md §7 colour map)."""
    st.subheader("Validation results")
    if not results:
        st.caption("No validation results yet.")
        return

    for r in results:
        color = _result_color(r)
        rule_id = r.get("rule_id", "")
        category = r.get("category", "")
        severity = r.get("severity", "")
        outcome = r.get("outcome", "")
        reason = r.get("reason", "")
        st.markdown(
            f"""
            <div style="border-left:6px solid {color};padding:6px 10px;margin:4px 0;
                        background:rgba(0,0,0,0.03);border-radius:4px;">
              <strong>{rule_id}</strong>
              <span style="color:{color};font-weight:600;"> [{outcome}]</span>
              <span style="opacity:0.7;"> · {category} · {severity}</span><br/>
              <span>{reason}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_risk_panel(decision: dict | None) -> None:
    """Risk panel derived from the decision's fraud-risk score."""
    st.subheader("Risk")
    if not decision:
        st.caption("No risk assessment yet.")
        return
    score = decision.get("fraud_risk_score")
    if score is None:
        st.caption("No fraud-risk score.")
        return
    if score >= 60:
        st.error(f"High fraud risk (score {score:g})")
    elif score >= 30:
        st.warning(f"Medium fraud risk (score {score:g})")
    else:
        st.success(f"Low fraud risk (score {score:g})")


def _render_communications(communications: list[dict]) -> None:
    """Editable email box prefilled from the generated communication."""
    st.subheader("Vendor communication")
    if not communications:
        st.caption("No communication generated (typically only for pending/rejected).")
        return
    comm = communications[0]
    st.text_input("Subject", value=comm.get("subject", ""), key="comm_subject")
    st.text_area("Body", value=comm.get("body", ""), height=200, key="comm_body")
    requested = comm.get("requested_items") or []
    if requested:
        st.write("**Requested items:**")
        for item in requested:
            st.write(f"• {item}")
    # Demo-only: "send" simply confirms; there is no outbound mail in this build.
    if st.button("✉️ Send email (demo)"):
        st.success("Email marked as sent (demo only — nothing is actually mailed).")


def _render_override(submission_id: str, decision: dict | None) -> None:
    """Reviewer override action (POST /decision/{id}/override)."""
    st.subheader("Override decision")
    if not decision:
        st.caption("Run the pipeline before overriding.")
        return
    with st.form("override_form"):
        new_status = st.selectbox("New status", options=_OVERRIDE_STATUSES)
        note = st.text_area("Override note")
        submitted = st.form_submit_button("Apply override")
    if submitted:
        if not note.strip():
            st.warning("Please add a note explaining the override.")
            return
        try:
            api_client.override_decision(submission_id, new_status, note)
            st.success(f"Decision overridden to {new_status}.")
            st.rerun()
        except ApiError as exc:
            st.error(exc.message)


def _result_color(result: dict) -> str:
    """Pick the row colour: green for pass, else by severity."""
    if result.get("outcome") == "pass":
        return _PASS_COLOR
    return _SEVERITY_COLORS.get(result.get("severity", ""), _PASS_COLOR)


def _fmt(value) -> str:
    """Format a numeric score for display, tolerating ``None``."""
    if value is None:
        return "—"
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)
