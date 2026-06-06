"""Agent 6 · Vendor Communication (Implementation.md §4, Architecture.md Part 4).

Drafts the vendor-facing email — requests missing info (PENDING) or explains a
rejection politely (REJECTED). Extends the shared
:class:`~agents.base.GeminiAgent`; on any failure the deterministic
:meth:`fallback` returns a templated email built from ``missing_items``.

Tone rules (Architecture.md Part 4 / Implementation.md §4):
    - PENDING  → list what is missing + how to resend.
    - REJECTED → respectful; never reveal internal fraud heuristics.
    - APPROVED → skip or send a brief welcome.

Input::

    final_status: str
    missing_items: list[str]
    vendor_name: str
    contact_email: str

Output schema::

    {"subject": "string", "body": "string", "requested_items": ["string"]}
"""

from __future__ import annotations

import os
from typing import Any

from agents.base import GeminiAgent

_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
_DEFAULT_PROMPT = os.path.join(_PROMPTS_DIR, "communication.txt")

COMMUNICATION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "requested_items": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["subject", "body", "requested_items"],
}


class VendorCommunicationAgent(GeminiAgent):
    """Draft a vendor-facing onboarding email (prose)."""

    name = "communication"
    action = "COMMUNICATION_GENERATED"
    temperature = 0.4
    response_schema = COMMUNICATION_SCHEMA

    def __init__(self, client: Any, prompt_path: str | None = None) -> None:
        super().__init__(client, prompt_path or _DEFAULT_PROMPT)

    def _build_parts(
        self, final_status: str, missing_items: list, vendor_name: str, contact_email: str
    ) -> list:
        """Combine the prompt with the status, missing items, and vendor identity."""
        prompt = self._load_prompt()
        items = "\n".join(f"- {m}" for m in (missing_items or [])) or "- (none)"
        return [
            f"{prompt}\n\n"
            f"Status: {final_status}\n"
            f"Vendor: {vendor_name}\n"
            f"Contact email: {contact_email}\n"
            f"Missing / unclear items:\n{items}"
        ]

    def run(
        self,
        final_status: str,
        missing_items: list,
        vendor_name: str,
        contact_email: str,
        submission_id: Any = None,
    ) -> dict:
        """Draft the email; fall back to a templated message on failure."""
        return super().run(
            final_status, missing_items, vendor_name, contact_email, submission_id=submission_id
        )

    def fallback(
        self, final_status: str, missing_items: list, vendor_name: str, contact_email: str
    ) -> dict:
        """Deterministic backup: templated email built from ``missing_items``."""
        name = vendor_name or "Vendor"
        status = (final_status or "").lower()
        items = [m for m in (missing_items or []) if m]

        if status == "pending":
            subject = "Action needed to complete your vendor onboarding"
            lines = [
                f"Dear {name},",
                "",
                "Thank you for your vendor onboarding submission. Before we can "
                "proceed, we need a few items to be completed or clarified:",
                "",
            ]
            lines += [f"  - {m}" for m in items] or ["  - Please review and resubmit the requested details."]
            lines += [
                "",
                "Please reply to this email with the corrected information or "
                "re-upload the relevant documents, and we will continue processing "
                "your application.",
                "",
                "Kind regards,",
                "Vendor Onboarding Team",
            ]
            body = "\n".join(lines)
            return {"subject": subject, "body": body, "requested_items": items}

        if status == "rejected":
            subject = "Update on your vendor onboarding submission"
            body = "\n".join(
                [
                    f"Dear {name},",
                    "",
                    "Thank you for your interest in becoming a registered vendor. "
                    "After reviewing your submission, we are unable to approve the "
                    "onboarding request at this time.",
                    "",
                    "If you believe this is in error or would like to provide "
                    "additional information, please reply to this email and our team "
                    "will be glad to assist.",
                    "",
                    "Kind regards,",
                    "Vendor Onboarding Team",
                ]
            )
            return {"subject": subject, "body": body, "requested_items": []}

        # Approved (or unknown) → brief welcome.
        subject = "Welcome — your vendor onboarding is approved"
        body = "\n".join(
            [
                f"Dear {name},",
                "",
                "Your vendor onboarding has been approved and your account is now "
                "active. We look forward to working with you.",
                "",
                "Kind regards,",
                "Vendor Onboarding Team",
            ]
        )
        return {"subject": subject, "body": body, "requested_items": []}
