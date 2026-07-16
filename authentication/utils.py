import ipaddress
import json
import logging
import secrets
import string
import threading
import urllib.request

from .email_templates import (
    build_registration_received_email_html,
    build_registration_received_email_text,
    build_approval_email_html,
    build_approval_email_text,
    build_rejection_email_html,
    build_rejection_email_text,
    company_contact_extra_images,
    send_branded_email,
)
from .models import LoginActivity, StaffActivityLog

logger = logging.getLogger(__name__)


def generate_temp_password(length=10):
    """Generates a random temporary password."""
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
            company.email, "We've got your registration", text_body, html_body,
            extra_images=company_contact_extra_images(),
        )
    except Exception:
        # Registration must still succeed even if mail fails — but log it,
        # since silently losing this used to mean no one ever found out.
        logger.exception("Failed to send registration-received email to %s", company.email)


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
            company.email, "Welcome to TIXA", text_body, html_body,
            extra_images=company_contact_extra_images(),
        )
    except Exception:
        logger.exception("Failed to send approval email to %s", company.email)


def send_rejection_email(company, reason=""):
    if not company.email:
        return
    contact_name = company.contact_name or "there"
    try:
        text_body = build_rejection_email_text(contact_name, company.company_name, reason)
        html_body = build_rejection_email_html(contact_name, company.company_name, reason)
        send_branded_email(
            company.email, "Update on your registration", text_body, html_body,
            extra_images=company_contact_extra_images(),
        )
    except Exception:
        logger.exception("Failed to send rejection email to %s", company.email)


def get_client_ip(request):
    """Real client IP, accounting for a reverse proxy's X-Forwarded-For header."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _is_public_ip(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_private or addr.is_reserved or addr.is_link_local)


def describe_location(ip):
    """Immediate, offline location label used the moment a LoginActivity row
    is created. Private/loopback addresses (the norm in dev/local-network
    use) are labelled plainly; a public IP gets a placeholder here and is
    then enriched with a real city/region/country in the background — see
    record_login_activity()."""
    if not ip:
        return "Unknown"
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.is_loopback:
        return "This server (localhost)"
    if addr.is_private:
        return f"Local network ({ip})"
    return ip


def _lookup_geolocation(ip):
    """Real city/region/country for a public IP, via ip-api.com's free,
    keyless JSON endpoint (not Google — Google's Geolocation API needs a
    billed Cloud project/API key, which this app doesn't have configured).
    Short timeout, no exception ever escapes — this must never be able to
    break a login."""
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,city,regionName,country"
        with urllib.request.urlopen(url, timeout=2.5) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != "success":
            return None
        parts = [p for p in (data.get("city"), data.get("regionName"), data.get("country")) if p]
        return ", ".join(parts) or None
    except Exception:
        logger.warning("IP geolocation lookup failed for %s", ip)
        return None


def _enrich_location_async(activity_id, ip):
    """Runs in a background thread so login itself never waits on a
    third-party service — updates the row's location once (if) it resolves."""
    location = _lookup_geolocation(ip)
    if location:
        try:
            LoginActivity.objects.filter(id=activity_id).update(location=location)
        except Exception:
            logger.exception("Failed to save resolved location for LoginActivity %s", activity_id)


def record_login_activity(request, user=None, phone_number="", status="success", failure_reason=""):
    """Writes one LoginActivity row. Safe to call for both known and unknown
    accounts (user=None covers a phone number that doesn't exist at all)."""
    ip = get_client_ip(request)
    company_name = ""
    if user is not None:
        company = getattr(user, "company", None)
        company_name = getattr(company, "company_name", "") or ""
    try:
        activity = LoginActivity.objects.create(
            user=user,
            full_name=(user.full_name if user else "") or "",
            phone_number=(user.phone_number if user else phone_number) or "",
            role=(user.role if user else "") or "",
            company_name=company_name,
            status=status,
            failure_reason=failure_reason,
            ip_address=ip,
            location=describe_location(ip),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:300],
        )
        if ip and _is_public_ip(ip):
            threading.Thread(target=_enrich_location_async, args=(activity.id, ip), daemon=True).start()
    except Exception:
        # Never let logging break an actual login.
        logger.exception("Failed to record login activity for %s", phone_number or (user and user.phone_number))


def log_staff_activity(request, action, description):
    """Writes one StaffActivityLog row for the acting staff member (request.user)."""
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return
    try:
        StaffActivityLog.objects.create(
            staff=user,
            full_name=user.full_name or "",
            phone_number=user.phone_number or "",
            action=action,
            description=description,
            ip_address=get_client_ip(request),
        )
    except Exception:
        logger.exception("Failed to record staff activity for %s", user.phone_number)