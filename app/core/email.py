"""
Email sending, behind an interface so swapping providers later is a
one-file change, not a rewrite of the password-reset flow itself.

ResendEmailSender is real and live once RESEND_API_KEY is set (see
app/core/config.py). Without that env var — local dev, tests, or a
fresh clone before it's configured — LoggingEmailSender logs the
reset link instead of sending it, so nothing breaks either way.
"""

import logging
from abc import ABC, abstractmethod

from app.core.config import settings

logger = logging.getLogger("sanchay.email")


class EmailSender(ABC):
    @abstractmethod
    def send_password_reset(self, to_email: str, reset_link: str) -> None: ...


class LoggingEmailSender(EmailSender):
    """
    DEV-ONLY. Does not send real email. Logs the reset link so it's
    usable during local development and testing. Replaces itself with
    ResendEmailSender automatically once RESEND_API_KEY is set — see
    the bottom of this module.
    """

    def send_password_reset(self, to_email: str, reset_link: str) -> None:
        logger.warning(
            "DEV EMAIL SENDER — no real email provider configured. "
            "Password reset for %s: %s",
            to_email,
            reset_link,
        )


class ResendEmailSender(EmailSender):
    """
    Real email delivery via Resend (https://resend.com). Free tier
    covers 3,000 emails/month, 100/day — comfortably enough for
    password resets at current scale.
    """

    def send_password_reset(self, to_email: str, reset_link: str) -> None:
        import resend

        resend.api_key = settings.resend_api_key
        resend.Emails.send(
            {
                "from": settings.reset_email_from,
                "to": [to_email],
                "subject": "Reset your Sanchay password",
                "html": f"""
                    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
                      <h2 style="color: #1C2541;">Reset your password</h2>
                      <p>Someone requested a password reset for your Sanchay account.
                         If this was you, click below — this link works once and
                         expires in {settings.password_reset_token_expire_minutes} minutes.</p>
                      <p style="margin: 28px 0;">
                        <a href="{reset_link}"
                           style="background: #1C2541; color: #fff; padding: 12px 24px;
                                  border-radius: 8px; text-decoration: none; display: inline-block;">
                          Reset password
                        </a>
                      </p>
                      <p style="color: #6B7280; font-size: 13px;">
                        If you didn't request this, you can safely ignore this email —
                        your password won't change unless you click the link above.
                      </p>
                    </div>
                """,
            }
        )


# Real sender when configured, dev-logging fallback otherwise — chosen
# once at import time, not per-request, so a missing key fails the
# same way on every call rather than being a surprise mid-request.
email_sender: EmailSender = ResendEmailSender() if settings.resend_api_key else LoggingEmailSender()
