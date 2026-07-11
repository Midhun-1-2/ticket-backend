"""HTML email templates for Ticket Desk's transactional mail (inlined, table-based layout)."""

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


# Shared shell — header/footer used by every email type.
def _wrap_email(eyebrow, title, body_html):
    return f"""<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background-color:#F7F6F3;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#F7F6F3;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background-color:#FFFFFF;border-radius:10px;overflow:hidden;border:1px solid #E5E2DA;">

            <!-- Header -->
            <tr>
              <td style="background-color:#14171F;padding:24px 28px;">
                <table role="presentation" cellpadding="0" cellspacing="0">
                  <tr>
                    <td style="width:36px;height:36px;border-radius:8px;background-color:#0F6E63;text-align:center;vertical-align:middle;">
                      <span style="color:#EAF6F3;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-weight:700;font-size:15px;line-height:36px;">TD</span>
                    </td>
                    <td style="padding-left:12px;" valign="middle">
                      <div style="color:#FFFFFF;font-size:16px;font-weight:700;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">Ticket Desk</div>
                      <div style="color:#7C8092;font-size:11.5px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">Admin Console</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>

            <!-- Body -->
            <tr>
              <td style="padding:32px 28px;">
                <div style="color:#A6A297;font-family:'Courier New',Courier,monospace;font-size:11.5px;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;">
                  {eyebrow}
                </div>
                <div style="color:#1C1E22;font-size:20px;font-weight:700;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin-bottom:16px;">
                  {title}
                </div>
                {body_html}
              </td>
            </tr>

            <!-- Footer -->
            <tr>
              <td style="background-color:#F7F6F3;padding:18px 28px;border-top:1px solid #E5E2DA;">
                <p style="color:#A6A297;font-size:11px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin:0;text-align:center;">
                  Ticket Desk &middot; This is an automated message, please don't reply.
                </p>
              </td>
            </tr>

          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _paragraph(text):
    return f"""<p style="color:#1C1E22;font-size:14px;line-height:1.6;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin:0 0 20px;">{text}</p>"""


def _details_card(rows, accent_bg="#E3F1EE", label_color="#0B4F47", border_color="rgba(15,110,99,0.15)"):
    """Renders a light card of label/value rows."""
    row_html = ""
    for label, value in rows:
        row_html += f"""
        <tr>
          <td style="padding:8px 0;border-bottom:1px solid {border_color};font-size:12.5px;color:{label_color};font-family:'Segoe UI',Helvetica,Arial,sans-serif;">{label}</td>
          <td style="padding:8px 0;border-bottom:1px solid {border_color};font-size:13px;font-weight:600;color:#1C1E22;font-family:'Segoe UI',Helvetica,Arial,sans-serif;text-align:right;">{value}</td>
        </tr>"""
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{accent_bg};border-radius:8px;margin-bottom:20px;">
      <tr>
        <td style="padding:16px 20px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            {row_html}
          </table>
        </td>
      </tr>
    </table>"""


def _text_block(label, text):
    """Free-text content in a plain bordered box (e.g. a ticket description)."""
    # Preserve line breaks, since email HTML collapses plain newlines.
    safe_text = (text or "").replace("\n", "<br>")
    return f"""
    <div style="margin-bottom:20px;">
      <div style="color:#6E6B62;font-size:11px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;">
        {label}
      </div>
      <div style="background-color:#F7F6F3;border:1px solid #E5E2DA;border-radius:8px;padding:12px 14px;color:#1C1E22;font-size:13px;line-height:1.6;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
        {safe_text}
      </div>
    </div>"""


def _otp_box(otp):
    return f"""
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#E3F1EE;border-radius:8px;margin-bottom:20px;">
      <tr>
        <td style="padding:20px;text-align:center;">
          <div style="color:#6E6B62;font-size:11px;font-family:'Segoe UI',Helvetica,Arial,sans-serif;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">
            Your OTP
          </div>
          <div style="color:#0B4F47;font-family:'Courier New',Courier,monospace;font-size:32px;font-weight:700;letter-spacing:10px;">
            {otp}
          </div>
        </td>
      </tr>
    </table>"""


def _footnote(text):
    return f"""<p style="color:#6E6B62;font-size:12.5px;line-height:1.6;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin:0;">{text}</p>"""


# OTP emails — M-PIN change and M-PIN forgot flows.

def build_otp_email_html(display_name, otp, title, intro_text):
    body = _paragraph(f"Hi {display_name},<br><br>{intro_text}") + _otp_box(otp) + _footnote(
        "This code expires in <strong>10 minutes</strong>. If you didn't request this, "
        "you can safely ignore this email &mdash; your account is still secure."
    )
    return _wrap_email("SECURITY &middot; VERIFICATION", title, body)


def build_otp_email_text(display_name, otp, title, intro_text):
    return (
        f"{title}\n\n"
        f"Hi {display_name},\n\n"
        f"{intro_text}\n\n"
        f"Your OTP: {otp}\n\n"
        f"This code expires in 10 minutes. If you didn't request this, "
        f"you can safely ignore this email — your account is still secure.\n\n"
        f"— Ticket Desk"
    )


# Ticket raised — sent to the customer right after they submit a ticket.

def build_ticket_raised_email_html(customer_name, ticket_id, subject, category, priority, product, description):
    intro = "Thanks for reaching out — we've received your ticket and a member of our team will pick it up shortly."
    details = _details_card([
        ("Ticket ID", str(ticket_id)[:8].upper()),
        ("Subject", subject),
        ("Category", category),
        ("Priority", priority),
        ("Product", product or "Not Applicable"),
    ])
    description_block = _text_block("Description", description) if description else ""
    body = (
        _paragraph(f"Hi {customer_name},<br><br>{intro}")
        + details
        + description_block
        + _footnote(
            "You can track this ticket's status any time from your Ticket Desk dashboard. "
            "We'll email you again once it's resolved."
        )
    )
    return _wrap_email("TICKET &middot; RECEIVED", "We've got your ticket", body)


def build_ticket_raised_email_text(customer_name, ticket_id, subject, category, priority, product, description):
    description_block = f"\nDescription:\n{description}\n" if description else ""
    return (
        f"We've got your ticket\n\n"
        f"Hi {customer_name},\n\n"
        f"Thanks for reaching out — we've received your ticket and a member of our "
        f"team will pick it up shortly.\n\n"
        f"Ticket ID: {str(ticket_id)[:8].upper()}\n"
        f"Subject: {subject}\n"
        f"Category: {category}\n"
        f"Priority: {priority}\n"
        f"Product: {product or 'Not Applicable'}\n"
        f"{description_block}\n"
        f"You can track this ticket's status any time from your Ticket Desk dashboard. "
        f"We'll email you again once it's resolved.\n\n"
        f"— Ticket Desk"
    )


# Ticket resolved — sent to the customer when their ticket is marked Resolved.

def build_ticket_resolved_email_html(customer_name, ticket_id, subject, resolved_by_name):
    intro = "Good news — your ticket has been marked as resolved."
    details = _details_card([
        ("Ticket ID", str(ticket_id)[:8].upper()),
        ("Subject", subject),
        ("Resolved By", resolved_by_name or "Ticket Desk Team"),
    ])
    body = _paragraph(f"Hi {customer_name},<br><br>{intro}") + details + _footnote(
        "If everything looks good, no action is needed. If the issue comes back or "
        "this wasn't fully resolved, you can reopen the ticket from your dashboard."
    )
    return _wrap_email("TICKET &middot; RESOLVED", "Your ticket has been resolved", body)


def build_ticket_resolved_email_text(customer_name, ticket_id, subject, resolved_by_name):
    return (
        f"Your ticket has been resolved\n\n"
        f"Hi {customer_name},\n\n"
        f"Good news — your ticket has been marked as resolved.\n\n"
        f"Ticket ID: {str(ticket_id)[:8].upper()}\n"
        f"Subject: {subject}\n"
        f"Resolved By: {resolved_by_name or 'Ticket Desk Team'}\n\n"
        f"If everything looks good, no action is needed. If the issue comes back or "
        f"this wasn't fully resolved, you can reopen the ticket from your dashboard.\n\n"
        f"— Ticket Desk"
    )


# Registration received — sent right after onboarding submit.

def build_registration_received_email_html(contact_name, company_name, company_code):
    intro = f"Thanks for registering <strong>{company_name}</strong> with Ticket Desk. Your registration is now under review."
    details = _details_card([
        ("Company", company_name),
        ("Reference Code", company_code or "—"),
        ("Status", "Under Review"),
    ])
    body = _paragraph(f"Hi {contact_name},<br><br>{intro}") + details + _footnote(
        "You'll get another email as soon as an admin approves the account — after "
        "that you can log in with the mobile number and password you just set."
    )
    return _wrap_email("REGISTRATION &middot; RECEIVED", "We've got your registration", body)


def build_registration_received_email_text(contact_name, company_name, company_code):
    return (
        f"We've got your registration\n\n"
        f"Hi {contact_name},\n\n"
        f"Thanks for registering {company_name} with Ticket Desk. Your registration "
        f"(reference {company_code}) is now under review.\n\n"
        f"You'll get another email as soon as an admin approves the account — after "
        f"that you can log in with the mobile number and password you just set.\n\n"
        f"— Ticket Desk"
    )


# Account approved — welcome email once a company is approved.

def build_approval_email_html(contact_name, company_name, phone_number):
    intro = f"Good news — <strong>{company_name}</strong> has been approved. Welcome to Ticket Desk!"
    details = _details_card([
        ("Company", company_name),
        ("Phone Number", phone_number),
        ("Password", "the one you set during registration"),
    ])
    body = _paragraph(f"Hi {contact_name},<br><br>{intro}") + details + _footnote(
        "Log in with the details above, and you'll be prompted to set an M-PIN on "
        "your first login for faster sign-ins after that."
    )
    return _wrap_email("ACCOUNT &middot; APPROVED", "Welcome to Ticket Desk", body)


def build_approval_email_text(contact_name, company_name, phone_number):
    return (
        f"Welcome to Ticket Desk\n\n"
        f"Hi {contact_name},\n\n"
        f"Good news — {company_name} has been approved on Ticket Desk.\n\n"
        f"You can now log in with:\n"
        f"Phone number: {phone_number}\n"
        f"Password: the one you set during registration\n\n"
        f"Please log in and set your M-PIN on first login.\n\n"
        f"— Ticket Desk"
    )


# Registration rejected — negative-outcome email, uses red/amber palette.

def build_rejection_email_html(contact_name, company_name, reason=""):
    intro = f"We're unable to approve the registration for <strong>{company_name}</strong> at this time."
    reason_block = ""
    if reason:
        reason_block = _details_card(
            [("Reason", reason)],
            accent_bg="#FAE6E1",
            label_color="#C4432E",
            border_color="rgba(196,67,46,0.2)",
        )
    body = _paragraph(f"Hi {contact_name},<br><br>{intro}") + reason_block + _footnote(
        "If you believe this is a mistake, please contact support and reference "
        f"{company_name} in your message."
    )
    return _wrap_email("REGISTRATION &middot; UPDATE", "Update on your registration", body)


def build_rejection_email_text(contact_name, company_name, reason=""):
    reason_line = f"\n\nReason: {reason}" if reason else ""
    return (
        f"Update on your registration\n\n"
        f"Hi {contact_name},\n\n"
        f"We're unable to approve the registration for {company_name} at this time."
        f"{reason_line}\n\n"
        f"If you believe this is a mistake, please contact support.\n\n"
        f"— Ticket Desk"
    )


# Shared sender used by every branded email above.

def send_branded_email(to_email, subject, text_body, html_body):
    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)