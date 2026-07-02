import secrets
import string
import uuid

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone


def generate_company_code():
    """e.g. CMP-2026-4F9A2B"""
    year = timezone.now().year
    suffix = uuid.uuid4().hex[:6].upper()
    return f"CMP-{year}-{suffix}"


def generate_temp_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _from_email():
    return getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@ticketdesk.local")


def send_registration_received_email(company):
    if not company.email:
        return
    subject = "Ticket Desk — Registration received"
    message = (
        f"Hi {company.contact_name or ''},\n\n"
        f"Thanks for registering {company.company_name} with Ticket Desk.\n"
        f"Your registration (reference {company.company_code}) is now under review.\n"
        "You'll receive another email with your login credentials once an "
        "admin approves the account.\n\n"
        "— Ticket Desk"
    )
    send_mail(subject, message, _from_email(), [company.email], fail_silently=True)


def send_approval_email(company, temp_password):
    if not company.email or not company.user:
        return
    subject = "Ticket Desk — Your account has been approved"
    message = (
        f"Hi {company.contact_name or ''},\n\n"
        f"Good news — {company.company_name} has been approved on Ticket Desk.\n\n"
        f"Login phone number: {company.user.phone_number}\n"
        f"Temporary password: {temp_password}\n\n"
        "Please log in and set your M-PIN on first login. We recommend "
        "changing your password after logging in.\n\n"
        "— Ticket Desk"
    )
    send_mail(subject, message, _from_email(), [company.email], fail_silently=True)


def send_rejection_email(company, reason=""):
    if not company.email:
        return
    subject = "Ticket Desk — Registration update"
    message = (
        f"Hi {company.contact_name or ''},\n\n"
        f"We're unable to approve the registration for {company.company_name} at this time."
        + (f"\n\nReason: {reason}" if reason else "")
        + "\n\nIf you believe this is a mistake, please contact support.\n\n— Ticket Desk"
    )
    send_mail(subject, message, _from_email(), [company.email], fail_silently=True)