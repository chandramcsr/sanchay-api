"""
Tests the sender-selection logic itself (Resend when configured,
logging fallback otherwise) without ever making a real network call
to Resend — that's neither necessary nor appropriate in a test suite.
"""

import importlib
import os
from unittest.mock import patch


def test_logging_sender_used_when_no_api_key_configured():
    from app.core.email import LoggingEmailSender, email_sender

    # conftest.py never sets RESEND_API_KEY, so this is the real
    # module-level state under test, not a re-import artifact.
    assert isinstance(email_sender, LoggingEmailSender)


def test_resend_sender_selected_when_api_key_is_configured():
    with patch.dict(os.environ, {"RESEND_API_KEY": "re_test_fake_key_not_real"}):
        import app.core.config as config_module
        import app.core.email as email_module

        importlib.reload(config_module)
        importlib.reload(email_module)

        assert isinstance(email_module.email_sender, email_module.ResendEmailSender)

    # Restore both modules to their normal (no-key) state so later
    # tests in the suite aren't affected by this one's env patching.
    importlib.reload(config_module)
    importlib.reload(email_module)


def test_resend_sender_calls_resend_api_with_correct_payload():
    from app.core.email import ResendEmailSender

    with patch("resend.Emails.send") as mock_send:
        with patch("app.core.email.settings") as mock_settings:
            mock_settings.resend_api_key = "re_test_fake_key"
            mock_settings.reset_email_from = "Sanchay <test@resend.dev>"
            mock_settings.password_reset_token_expire_minutes = 30

            ResendEmailSender().send_password_reset("user@example.com", "https://example.com/reset?token=abc")

        assert mock_send.called
        call_kwargs = mock_send.call_args[0][0]
        assert call_kwargs["to"] == ["user@example.com"]
        assert "reset?token=abc" in call_kwargs["html"]
        assert call_kwargs["from"] == "Sanchay <test@resend.dev>"
