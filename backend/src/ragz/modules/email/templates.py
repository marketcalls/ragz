"""Pure template functions: (subject, html, text) tuples for transactional
email. No template engine -- inline HTML + a plain-text fallback, kept
deliberately simple. Any interpolated value is HTML-escaped defensively even
when app-generated (e.g. `reset_url`) so a future caller can't accidentally
turn this into an injection vector by passing untrusted input.
"""

from html import escape

_FOOTER_HTML = (
    '<p style="color:#666;font-size:12px">'
    "If you did not request this, you can safely ignore this email.</p>"
)
_FOOTER_TEXT = "If you did not request this, you can safely ignore this email."


def reset_password_email(reset_url: str, *, ttl_minutes: int) -> tuple[str, str, str]:
    """Password-reset link email. `ttl_minutes` is surfaced so the recipient
    knows the link expires."""
    safe_url = escape(reset_url)
    subject = "Reset your Ragz password"
    html = (
        "<p>We received a request to reset your Ragz password.</p>"
        f'<p><a href="{safe_url}">Click here to reset your password</a></p>'
        f"<p>This link expires in {ttl_minutes} minutes.</p>"
        f"{_FOOTER_HTML}"
    )
    text = (
        "We received a request to reset your Ragz password.\n\n"
        f"Reset your password: {reset_url}\n\n"
        f"This link expires in {ttl_minutes} minutes.\n\n"
        f"{_FOOTER_TEXT}"
    )
    return subject, html, text


def password_changed_email() -> tuple[str, str, str]:
    """Confirmation sent after a successful password change/reset."""
    subject = "Your Ragz password was changed"
    html = (
        "<p>Your Ragz account password was just changed.</p>"
        "<p>If you made this change, no further action is needed.</p>"
        f"{_FOOTER_HTML}"
    )
    text = (
        "Your Ragz account password was just changed.\n\n"
        "If you made this change, no further action is needed.\n\n"
        f"{_FOOTER_TEXT}"
    )
    return subject, html, text


def test_email() -> tuple[str, str, str]:
    """Sent by the admin "send test email" action to confirm provider config."""
    subject = "Ragz test email"
    html = "<p>This is a test email from your Ragz installation.</p><p>Delivery is working.</p>"
    text = "This is a test email from your Ragz installation.\n\nDelivery is working."
    return subject, html, text
