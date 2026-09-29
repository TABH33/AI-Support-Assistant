"""Tests for app.integrations.email -- the opt-in escalation email.

`smtplib.SMTP` is patched in every test that could reach it -- no real
network, the same philosophy as test_openrouteservice.py's `httpx` mocking.
The autouse fixture below configures a complete, fake SMTP setup; the
not-configured tests opt out by monkeypatching individual settings back to
"".
"""
from __future__ import annotations

import logging
import smtplib
from unittest.mock import patch

import pytest

from app.config import settings
from app.integrations.email import (
    EmailDeliveryError,
    EmailError,
    EmailNotConfiguredError,
    send_escalation_email,
)

_SEND_KWARGS = dict(
    customer_name="Alice Customer",
    customer_email="alice@example.test",
    question="Why is my device offline?",
    answer="I'm not able to answer that confidently.",
    support_ticket_id=901,
)


@pytest.fixture(autouse=True)
def configured_smtp(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "bot@example.test")
    monkeypatch.setattr(settings, "smtp_password", "s3cret-smtp-password")
    monkeypatch.setattr(settings, "smtp_from_address", "support-bot@example.test")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    monkeypatch.setattr(settings, "escalation_email_to", "escalations@example.test")


def _smtp_instance(mock_smtp_cls):
    """The object bound by `with smtplib.SMTP(...) as smtp:`."""
    return mock_smtp_cls.return_value.__enter__.return_value


def _sent_message(mock_smtp_cls):
    return _smtp_instance(mock_smtp_cls).send_message.call_args[0][0]


def test_sends_one_message_over_starttls_with_login():
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        send_escalation_email(**_SEND_KWARGS)

    mock_smtp_cls.assert_called_once_with("smtp.example.test", 587, timeout=15.0)
    smtp = _smtp_instance(mock_smtp_cls)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("bot@example.test", "s3cret-smtp-password")
    smtp.send_message.assert_called_once()


def test_message_headers_address_the_configured_inbox():
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        send_escalation_email(**_SEND_KWARGS)

    message = _sent_message(mock_smtp_cls)
    assert message["To"] == "escalations@example.test"
    assert message["From"] == "support-bot@example.test"
    assert message["Reply-To"] == "alice@example.test"
    assert "901" in message["Subject"]


def test_message_body_carries_name_question_answer_and_ticket_reference():
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        send_escalation_email(**_SEND_KWARGS)

    body = _sent_message(mock_smtp_cls).get_content()
    assert "Alice Customer" in body
    assert "Why is my device offline?" in body
    assert "I'm not able to answer that confidently." in body
    assert "#901" in body


def test_from_address_falls_back_to_the_smtp_username(monkeypatch):
    monkeypatch.setattr(settings, "smtp_from_address", "")
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        send_escalation_email(**_SEND_KWARGS)

    assert _sent_message(mock_smtp_cls)["From"] == "bot@example.test"


def test_skips_starttls_when_tls_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "smtp_use_tls", False)
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        send_escalation_email(**_SEND_KWARGS)

    smtp = _smtp_instance(mock_smtp_cls)
    smtp.starttls.assert_not_called()
    smtp.send_message.assert_called_once()


def test_skips_login_when_no_username_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_username", "")
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        send_escalation_email(**_SEND_KWARGS)

    smtp = _smtp_instance(mock_smtp_cls)
    smtp.login.assert_not_called()
    smtp.send_message.assert_called_once()


def test_unconfigured_host_raises_immediately_without_connecting(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        with pytest.raises(EmailNotConfiguredError, match="SMTP_HOST is not configured"):
            send_escalation_email(**_SEND_KWARGS)

    mock_smtp_cls.assert_not_called()


def test_missing_from_address_and_username_raises_without_connecting(monkeypatch):
    monkeypatch.setattr(settings, "smtp_from_address", "")
    monkeypatch.setattr(settings, "smtp_username", "")
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        with pytest.raises(EmailNotConfiguredError):
            send_escalation_email(**_SEND_KWARGS)

    mock_smtp_cls.assert_not_called()


def test_missing_recipient_raises_without_connecting(monkeypatch):
    monkeypatch.setattr(settings, "escalation_email_to", "")
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        with pytest.raises(EmailNotConfiguredError, match="ESCALATION_EMAIL_TO"):
            send_escalation_email(**_SEND_KWARGS)

    mock_smtp_cls.assert_not_called()


def test_smtp_failure_is_wrapped_with_the_underlying_message_preserved():
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        _smtp_instance(mock_smtp_cls).login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted"
        )
        with pytest.raises(EmailDeliveryError) as exc_info:
            send_escalation_email(**_SEND_KWARGS)

    assert "Username and Password not accepted" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, smtplib.SMTPAuthenticationError)


def test_connection_failure_is_wrapped_as_a_delivery_error():
    with patch(
        "app.integrations.email.smtplib.SMTP",
        side_effect=ConnectionRefusedError("Connection refused"),
    ):
        with pytest.raises(EmailDeliveryError, match="Connection refused"):
            send_escalation_email(**_SEND_KWARGS)


def test_error_hierarchy_has_one_catchable_base():
    assert issubclass(EmailError, RuntimeError)
    assert issubclass(EmailNotConfiguredError, EmailError)
    assert issubclass(EmailDeliveryError, EmailError)


def test_smtp_password_is_never_logged_or_put_in_the_exception(caplog):
    caplog.set_level(logging.DEBUG, logger="app.integrations.email")
    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        _smtp_instance(mock_smtp_cls).login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"auth failed"
        )
        with pytest.raises(EmailDeliveryError) as exc_info:
            send_escalation_email(**_SEND_KWARGS)

    assert caplog.records, "the failure should still be logged"
    assert "s3cret-smtp-password" not in caplog.text
    assert "s3cret-smtp-password" not in str(exc_info.value)
