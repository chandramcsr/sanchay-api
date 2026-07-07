"""
Email sending, behind an interface so swapping in a real provider
later (Resend, SendGrid, Postmark — any of them) is a one-file change,
not a rewrite of the password-reset flow itself.

No provider is wired up yet — that needs an account and API key from
whoever owns this deployment, which isn't something to invent here.
The dev implementation logs the email content (including the reset
link) to stdout instead of sending it, clearly marked as such. In
development this is actually convenient (copy the link straight from
the server log); it must not ship to a real deployment serving real
users without a real sender wired in — anyone who forgets their
password would have no way to actually receive the reset link.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("sanchay.email")


class EmailSender(ABC):
    @abstractmethod
    def send_password_reset(self, to_email: str, reset_link: str) -> None: ...


class LoggingEmailSender(EmailSender):
    """
    DEV-ONLY. Does not send real email. Logs the reset link so it's
    usable during local development and testing. Replace with a real
    provider (see module docstring) before this handles real users.
    """

    def send_password_reset(self, to_email: str, reset_link: str) -> None:
        logger.warning(
            "DEV EMAIL SENDER — no real email provider configured. "
            "Password reset for %s: %s",
            to_email,
            reset_link,
        )


# Swap this for a real implementation once a provider is chosen, e.g.:
#
#   class ResendEmailSender(EmailSender):
#       def send_password_reset(self, to_email: str, reset_link: str) -> None:
#           resend.Emails.send({
#               "from": "Sanchay <noreply@yourdomain.com>",
#               "to": to_email,
#               "subject": "Reset your Sanchay password",
#               "html": f"<a href='{reset_link}'>Reset password</a> (expires in 30 minutes)",
#           })
#
# ...then change this one line:
email_sender: EmailSender = LoggingEmailSender()
