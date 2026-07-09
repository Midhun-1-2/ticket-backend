import secrets
import string
import uuid

from django.utils import timezone

from .email_templates import (
    build_registration_received_email_html,
    build_registration_received_email_text,
    build_approval_email_html,
    build_approval_email_text,
    build_rejection_email_html,
    build_rejection_email_text,
    send_branded_email,
)


def generate_company_code():
    """e.g. CMP-2026-4F9A2B"""
    year = timezone.now().year
    suffix = uuid.uuid4().hex[:6].upper()
    return f"CMP-{year}-{suffix}"


def generate_temp_password(length=10):
    """Kept for reference / possible future use (e.g. admin-initiated
    password resets). No longer used in the onboarding approval flow now
    that customers set their own password at signup."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def send_registration_received_email(company):
    if not company.email:
        return
    contact_name = company.contact_name or "there"
    try:
        text_body = build_registration_received_email_text(
            contact_name, company.company_name, company.company_code
        )
        html_body = build_registration_received_email_html(
            contact_name, company.company_name, company.company_code
        )
        send_branded_email(
            company.email, "We've got your registration", text_body, html_body
        )
    except Exception:
        # Registration must succeed even if the mail server is down —
        # same fail-silently reasoning as ticketapp's email helpers.
        pass


def send_approval_email(company):
    if not company.email or not company.user:
        return
    contact_name = company.contact_name or "there"
    try:
        text_body = build_approval_email_text(
            contact_name, company.company_name, company.user.phone_number
        )
        html_body = build_approval_email_html(
            contact_name, company.company_name, company.user.phone_number
        )
        send_branded_email(
            company.email, "Welcome to Ticket Desk", text_body, html_body
        )
    except Exception:
        pass


def send_rejection_email(company, reason=""):
    if not company.email:
        return
    contact_name = company.contact_name or "there"
    try:
        text_body = build_rejection_email_text(contact_name, company.company_name, reason)
        html_body = build_rejection_email_html(contact_name, company.company_name, reason)
        send_branded_email(
            company.email, "Update on your registration", text_body, html_body
        )
    except Exception:
        pass