"""
HTML email templates for Ticket Desk's transactional mail — currently just
the OTP email used by both the Change M-PIN (authenticated) and Forgot
M-PIN (unauthenticated) flows.

Email clients don't reliably support external stylesheets, CSS variables,
or web fonts, so this can't literally reuse style.css — everything here is
inlined and uses table-based layout for compatibility (Outlook especially
needs tables, not flexbox/grid). The colors below are copied directly from
style.css's :root tokens so the email still reads as "the same product":
  --ink:      #14171F
  --accent:   #0F6E63
  --accent-ink: #0B4F47
  --accent-soft: #E3F1EE
  --paper:    #F7F6F3
  --text:     #1C1E22
  --text-muted: #6E6B62
  --text-faint: #A6A297
  --line:     #E5E2DA
"""


def build_otp_email_html(display_name, otp, title, intro_text):
    """
    display_name: user's full name (or phone number as a fallback)
    otp:          the 4-digit code, as a string
    title:        short heading, e.g. "Change your M-PIN" / "Reset your M-PIN"
    intro_text:   one sentence of context shown above the OTP box
    """
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
                  SECURITY &middot; VERIFICATION
                </div>
                <div style="color:#1C1E22;font-size:20px;font-weight:700;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin-bottom:16px;">
                  {title}
                </div>
                <p style="color:#1C1E22;font-size:14px;line-height:1.6;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin:0 0 20px;">
                  Hi {display_name},<br><br>
                  {intro_text}
                </p>

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
                </table>

                <p style="color:#6E6B62;font-size:12.5px;line-height:1.6;font-family:'Segoe UI',Helvetica,Arial,sans-serif;margin:0;">
                  This code expires in <strong>10 minutes</strong>. If you didn't request this, you can safely ignore this email &mdash; your account is still secure.
                </p>
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


def build_otp_email_text(display_name, otp, title, intro_text):
    """Plain-text fallback for clients that don't render HTML (or have it
    disabled). Content matches the HTML version, just unstyled."""
    return (
        f"{title}\n\n"
        f"Hi {display_name},\n\n"
        f"{intro_text}\n\n"
        f"Your OTP: {otp}\n\n"
        f"This code expires in 10 minutes. If you didn't request this, "
        f"you can safely ignore this email — your account is still secure.\n\n"
        f"— Ticket Desk"
    )
