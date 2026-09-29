"""Thin SMTP client for the opt-in chat escalation email.

Mirrors app/integrations/openrouteservice.py's shape: one module owning a
single outbound integration, its own error hierarchy, configuration read
from app.config.settings (never hardcoded), and no FastAPI/database imports.
Standard library only (`smtplib` + `email.message.EmailMessage` + `ssl`) --
no new dependency.

Raises on every failure rather than swallowing it. The caller
(`POST /chat/messages/{id}/escalate` in app/api/chat.py) decides what a
failure means -- it has already committed the support ticket by then, so it
reports `email_sent=false` instead of failing the request -- the same split
as build_route_plan translating a RouteServiceError into
`unavailable=True`. There is no automatic retry (this app has no
background-job infrastructure to retry against).

An empty SMTP host/sender/recipient is treated as "not configured" and
raises EmailNotConfiguredError before any connection is attempted (same
convention as openrouteservice.py's empty-API-key guard). The SMTP password
is only ever handed to `smtplib.SMTP.login` -- it is never logged and never
included in an exception message.

See docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 15.0

_MISSING_HOST_MESSAGE = "SMTP_HOST is not configured"
_MISSING_FROM_MESSAGE = "SMTP_FROM_ADDRESS (or SMTP_USERNAME) is not configured"
_MISSING_TO_MESSAGE = "ESCALATION_EMAIL_TO is not configured"


class EmailError(RuntimeError):
    """Base exception for this module's failures."""


class EmailNotConfiguredError(EmailError):
    """SMTP isn't configured (empty host, sender, or recipient) -- raised
    before any connection is attempted."""


class EmailDeliveryError(EmailError):
    """The SMTP conversation itself failed (connection, TLS, auth, or the
    server rejecting the message). The underlying exception is chained as
    `__cause__` and its message is preserved in this one's."""


def _require_config() -> tuple[str, str, str]:
    """Return `(host, from_address, to_address)`, or raise
    EmailNotConfiguredError naming the first missing piece."""
    host = settings.smtp_host
    if not host:
        logger.error(_MISSING_HOST_MESSAGE)
        raise EmailNotConfiguredError(_MISSING_HOST_MESSAGE)

    from_address = settings.smtp_from_address or settings.smtp_username
    if not from_address:
        logger.error(_MISSING_FROM_MESSAGE)
        raise EmailNotConfiguredError(_MISSING_FROM_MESSAGE)

    to_address = settings.escalation_email_to
    if not to_address:
        logger.error(_MISSING_TO_MESSAGE)
        raise EmailNotConfiguredError(_MISSING_TO_MESSAGE)

    return host, from_address, to_address


def _build_message(
    *,
    from_address: str,
    to_address: str,
    customer_name: str,
    customer_email: str,
    question: str,
    answer: str,
    support_ticket_id: int,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"Chat escalation: support ticket #{support_ticket_id}"
    message["From"] = from_address
    message["To"] = to_address
    if customer_email:
        # Lets the support agent reply straight to the customer.
        message["Reply-To"] = customer_email
    message.set_content(
        "A customer asked for a human to follow up on a chat the AI assistant "
        "could not confidently answer.\n"
        "\n"
        f"Support ticket reference: #{support_ticket_id}\n"
        f"Customer: {customer_name} <{customer_email}>\n"
        "\n"
        "Customer's question:\n"
        f"{question}\n"
        "\n"
        "Assistant's answer:\n"
        f"{answer}\n"
    )
    return message


def send_escalation_email(
    *,
    customer_name: str,
    customer_email: str,
    question: str,
    answer: str,
    support_ticket_id: int,
) -> None:
    """Send one escalation email to `settings.escalation_email_to`.

    Raises EmailNotConfiguredError (no connection attempted) when SMTP isn't
    configured, or EmailDeliveryError when the SMTP conversation fails.
    Returns None on success."""
    host, from_address, to_address = _require_config()
    message = _build_message(
        from_address=from_address,
        to_address=to_address,
        customer_name=customer_name,
        customer_email=customer_email,
        question=question,
        answer=answer,
        support_ticket_id=support_ticket_id,
    )

    try:
        with smtplib.SMTP(host, settings.smtp_port, timeout=_DEFAULT_TIMEOUT_SECONDS) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # `exc` comes from the server/socket, never from our own settings,
        # so it cannot contain the password -- safe to log and to chain.
        logger.error(
            "Escalation email for support ticket #%s could not be sent: %s",
            support_ticket_id,
            exc,
        )
        raise EmailDeliveryError(f"Escalation email could not be sent: {exc}") from exc
