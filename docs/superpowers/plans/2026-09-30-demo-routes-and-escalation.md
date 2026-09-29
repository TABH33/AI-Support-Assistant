# Demo Route Data, Driver-Aware Overviews, and Opt-In Escalation Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give customers and support agents something real to see when they ask about route problems or a route overview (a manually-run demo-route seed script plus wider chat intent keywords), name the driver on every route line in chat and reports, and replace the silent low-confidence auto-escalation with an explicit Yes/No offer that creates a ticket and emails a human only when the customer says yes.

**Architecture:** A new stdlib-only `app/integrations/email.py` (shaped like `openrouteservice.py`) sends one escalation email over SMTP, configured by seven new optional `Settings` fields. `app.ai.escalation.handle_answer` stops touching the database: below the confidence threshold it only returns `escalation_offered=True`, which `POST /chat` passes through on `ChatResponse`; a new `POST /chat/messages/{id}/escalate` endpoint (ownership-checked exactly like the feedback endpoint) creates the ticket through the existing idempotent select-first/SAVEPOINT helper, commits, and only then best-effort sends the email. `summarize_todays_routes` and `_format_route_risk_warnings` gain a batched driver-name lookup, `_detect_todays_routes_intent` gains five signal words, the chat widget renders the Yes/No prompt, and `python -m app.seed.seed_demo_routes` writes synthetic today-dated `RoutePlan` rows with no network access.

**Tech Stack:** FastAPI + SQLAlchemy 2.x (backend, Python 3.11+), stdlib `smtplib`/`email.message`/`ssl`, React 18 + TypeScript + Vite + Tailwind (frontend), pytest + Vitest/Testing Library (tests). No new dependencies, no schema change, no Alembic migration.

**Spec:** docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md

## Global Constraints

- **No schema changes.** No new column, table, or Alembic migration. Everything uses existing `RoutePlan` (`driver_id`, `warnings`, `status`, `created_at`, `completed_at`), `ChatMessage`, `SupportTicket`, `Notification` columns.
- **No new dependencies.** Email uses stdlib `smtplib` + `email.message.EmailMessage` (+ `ssl` for STARTTLS). Frontend adds nothing.
- **New `Settings` fields, verbatim from the spec, all optional/empty-by-default like `ors_api_key`:** `smtp_host: str = ""`, `smtp_port: int = 587`, `smtp_username: str = ""`, `smtp_password: str = ""`, `smtp_from_address: str = ""`, `smtp_use_tls: bool = True`, `escalation_email_to: str = "CIHE241731@student.edu.cihe.au"`.
- **`send_escalation_email(*, customer_name: str, customer_email: str, question: str, answer: str, support_ticket_id: int) -> None`** raises on every failure (never swallows); an empty `smtp_host` raises immediately without connecting; the SMTP password is never logged and never appears in an exception message. No automatic retry.
- **Two failure domains.** The escalate endpoint commits the ticket + notification FIRST, then sends the email inside its own `try/except`. An email failure degrades to `email_sent=false` — never a rollback of the committed ticket, never a 5xx.
- **`escalation_offered: bool` is a NEW field** on `EscalationResult` and `ChatResponse`. `escalated` keeps its meaning ("a ticket now exists for this exchange") and is never set `True` implicitly by `handle_answer`/`POST /chat` again.
- **Escalate endpoint ownership mirrors `submit_message_feedback` exactly:** `require_role("customer", "support_agent")`; `db.get(ChatMessage, ...)` → 404 if missing; 404 (not 403) if a `customer` caller doesn't own the resolved `ChatSession.customer_id`; 400 if the message isn't `role=ASSISTANT`; `support_agent` unrestricted. Response is exactly `{support_ticket_id: int, email_sent: bool}`.
- **Double-click safety:** ticket creation goes through `app.ai.escalation`'s select-first + `IntegrityError`-in-SAVEPOINT helper; one ticket per session, ever. The in-app `Notification` keeps `notification_type=PreferredNotificationMethod.IN_APP`.
- **Chat "today's routes" answers (now with driver names) stay scoped to `chat_session.customer_id` — never fleet-wide.** This preserves the fixed vulnerability documented in `docs/SECURITY.md`. Do not add any fleet-wide chat/report path. The fleet-wide surface remains `GET /route-plans`.
- **Widened intent words, verbatim:** `_TODAYS_ROUTES_TEMPORAL_STATUS_WORDS` = `"today"`, `"active"`, `"problem"`, `"problems"`, `"issue"`, `"issues"`, `"overview"`. A route word (`"route"`/`"routes"`) is still required too.
- **Demo route data is generated once, by a manual script** (`python -m app.seed.seed_demo_routes`) — never regenerated per question, no ORS/network/API-key dependency, docstring states it is synthetic demo data and not real incident detection. Re-running adds another batch.
- **Seeded warnings use the exact stored shape** `save_route_plan` writes for a real `Warning`: `{"location": {"lat", "lon"}, "distance_from_origin_km", "type": "weather"|"risk_zone", "severity": "moderate"|"high", "description"}` — so `WarningOut`, reports, chat and the live map treat them identically, with no format branching anywhere else.
- **"Today" is Sydney-local** — always via `app.timeutil` (`site_today()`/`site_day_bounds()`/`SITE_TZ`), never a raw UTC date.
- **Helpers that write rows flush, never commit** (`create_support_ticket`, `create_notification`, `get_or_create_escalation_ticket`, `seed_demo_routes`); the request handler / script entry point owns the single `db.commit()`.
- **Exact UI copy:** prompt `Would you like to escalate this to a human?` with buttons `Yes` / `No`; confirmation `A support agent has been notified (ref #<support_ticket_id>)`, plus ` (email notification could not be sent)` appended when `email_sent` is `false`. **No** sends no request.
- **Line numbers** below are as of commit `cbbea10` (this branch's spec commit). Earlier tasks shift later line numbers in shared files — always locate an edit by the quoted anchor text, and use the line numbers only as a hint.

---

## Spec coverage map

| Spec requirement | Task |
|---|---|
| `app/integrations/email.py`, `send_escalation_email`, empty-host raises, password never logged | 1 |
| Seven new `Settings` fields | 1 |
| (gap) SMTP vars must reach the container — `docker-compose.yml` enumerates env explicitly | 1 |
| Widen `_TODAYS_ROUTES_TEMPORAL_STATUS_WORDS` + false-positive regression tests | 2 |
| Driver name in `summarize_todays_routes` (assigned + unassigned) | 3 |
| Driver name in `_format_route_risk_warnings` (both reports) | 3 |
| `handle_answer` no longer creates ticket/notification; `escalation_offered` on `EscalationResult` + `ChatResponse` | 4 |
| `POST /chat/messages/{id}/escalate`: ownership, idempotency, commit-then-email, `{support_ticket_id, email_sent}` | 5 |
| `ChatWidget` Yes/No prompt, confirmation copy, No = local only | 6 |
| `app/seed/seed_demo_routes.py` + smoke test through real model/validators | 7 |
| `DEPLOYMENT.md` seeding + SMTP setup, `SECURITY.md`, `API_REFERENCE.md`, `RAG_PIPELINE.md` (+ `ARCHITECTURE.md`, `ROUTE_PLANNING.md`, `README.md` which describe the same behavior) | 8 |

## Spec gaps found while planning, and how this plan resolves them

1. **SMTP settings would never reach the container.** `docker-compose.yml`'s `backend.environment` block lists every variable explicitly (no `env_file:`), exactly the trap `docs/DEPLOYMENT.md` already documents for `ORS_API_KEY`. Task 1 adds all seven `SMTP_*`/`ESCALATION_EMAIL_TO` lines there and to both `.env.example` files.
2. **Seeded warnings need more than `type`/`severity`/`description`.** The spec says "the exact shape `Warning` produces", but what every consumer actually reads is the *stored* dict `save_route_plan` writes, and `WarningOut` (`app/api/route_plan.py`) *requires* `location` and `distance_from_origin_km`. Task 7 writes the full stored shape; its smoke test validates every row through `_row_to_list_item` → `WarningOut`.
3. **Widening "problem"/"issue" reopens the exact collision the "risk" fix closed.** `app/api/chat.py`'s own comment cites "are route deviations a risk to my fleet?" as the canonical must-not-hijack question; with the spec's word list and unchanged logic, "are route deviations an **issue** for my fleet?" and "I have a **problem** with route deviation alerts" would be hijacked away from RAG (route deviation is real domain vocabulary: `DrivingEventType.ROUTE_DEVIATION` and the seeded "Route deviation alerts explained" KB article). Task 2 adds one narrow guard — a query mentioning `"deviation"` is never this intent — and pins it with regression tests. This is the only logic change beyond the word list; a reviewer may reject it independently of the word-list change.
4. **Reports need no extra driver query.** Both report generators already batch-fetch `drivers_by_id` via `ds.list_drivers(customer_id)`, and every `RoutePlan.driver_id` is validated against its own customer's fleet on write, so Task 3 passes that existing map into `_format_route_risk_warnings` instead of issuing the "single extra query" the spec describes (same outcome, one fewer query). `summarize_todays_routes` has no such map, so it gets exactly one batched `Driver` query.
5. **`handle_answer` no longer needs a `Session`.** Once it stops creating rows it touches nothing in the database, so Task 4 narrows its signature to `handle_answer(chat_answer)` (one production caller). The ticket helper becomes public `get_or_create_escalation_ticket(db, chat_session, *, description, notification_type=IN_APP)` since the escalate endpoint has no `ChatAnswer` — only the persisted messages.
6. **Where the email's "customer's question" comes from.** Only the assistant `ChatMessage` id is posted, so Task 5 reads the `role=user` message immediately preceding it in the same session.
7. **Email on a reused ticket.** A second "Yes" (or a session that already has a thumbs-down ticket) reuses the ticket; Task 5 still attempts the email every time rather than returning `email_sent=false` for a mail it never tried (which the UI would render as a failure) — duplicate-email risk from a real double-click is closed in the UI (Task 6 disables Yes while the request is in flight). Documented in Task 8.

---

## Pre-flight file-region conflict scan

Tasks run strictly in order (1 → 8). Where two tasks touch one file, the regions below do not overlap, so neither task's edit can clobber the other's; later tasks must still locate edits by anchor text, since earlier tasks shift line numbers.

| File | Task | Region (line numbers at `cbbea10`) |
|---|---|---|
| `backend/app/api/chat.py` | 2 | `_TODAYS_ROUTES_*` constants + `_detect_todays_routes_intent`, lines 308-331 |
| `backend/app/api/chat.py` | 4 | `ChatResponse` class, lines 109-124; `post_chat` branch assignments / audit description / return, lines 443-557 |
| `backend/app/api/chat.py` | 5 | import block, lines 35-73; feedback-section comment block + two docstrings, lines 670-733; NEW endpoint appended after `submit_message_feedback` (after line 810, before `class CesSurveyRequest`) |
| `backend/app/ai/escalation.py` | 4 | whole file replaced. **Task 5 does not edit this file** — it only imports `get_or_create_escalation_ticket` from it |
| `backend/tests/test_chat_api.py` | 2 | append at end of file |
| `backend/tests/test_chat_api.py` | 4 | import block lines 54-65; two tests at lines 681-774; two lines at 862-865; append at end of file (after Task 2's appended block) |
| `.env.example` (root) | 1 | insert after line 44 (`ORS_API_KEY=`) |
| `.env.example` (root) | 4 | lines 34-37 (`# Escalation Configuration` comment) |
| `backend/app/ai/route_planning.py` | 3 | imports lines 30-32; new helper + `summarize_todays_routes`, lines 472-518 |
| `backend/app/ai/reports.py` | 3 | imports 80-87; `_format_route_risk_warnings` 239-262; call sites 376 and 416 |
| `frontend/src/components/ChatWidget.tsx`, `ChatWidget.test.tsx`, `types/chat.ts` | 6 | only task touching them |
| `docs/*.md` | 8 | only task touching them |

Task 4 → Task 5 on `app/api/chat.py` in detail: Task 4 edits only the `ChatResponse` model and the body of `post_chat` (lines 109-124, 443-557) and leaves the `from app.ai.escalation import handle_answer` line untouched. Task 5 edits the import block above line 73, three comment/docstring passages inside the feedback section (670-733), and appends a new block below `submit_message_feedback` (after 810). No line is edited by both. On `app/ai/escalation.py`, Task 4 is the only writer; Task 5 is a pure consumer of the signature Task 4 produces.

## Dispatch guidance (per `subagent-driven-development`'s Model Selection)

| Task | Grade | Why |
|---|---|---|
| 1 | Mechanical | New module mirrors `openrouteservice.py`; every line given |
| 2 | Mechanical | Word-list change + transcribed tests (high test count, low logic) |
| 3 | Judgment (standard) | Two call sites, batched-query pattern, report context strings |
| 4 | Judgment (most capable) | Changes existing behavior; must update every existing test/caller of `escalated` without weakening the suite |
| 5 | Judgment (most capable) | Auth/ownership mirroring, idempotency, commit-then-email ordering, two failure domains |
| 6 | Judgment (standard) | New per-message UI state machine, async error rollback |
| 7 | Mechanical-ish (standard) | Transcription, but real DB lookups and time-window arithmetic worth a careful reviewer |
| 8 | Mechanical | Verbatim doc insertions |

---

## Task 1: `app/integrations/email.py` + SMTP settings

**Dispatch:** Mechanical — transcription only. A fast/cheap implementer is sufficient; reviewer checks the password never reaches a log line or exception message.

**Files:**
- Create: `backend/app/integrations/email.py`
- Modify: `backend/app/config.py` (insert after the `ors_api_key: str = ""` line, line 28)
- Modify: `docker-compose.yml` (insert after `ORS_API_KEY: ${ORS_API_KEY:-}`, line 54)
- Modify: `.env.example` (insert after `ORS_API_KEY=`, line 44)
- Modify: `backend/.env.example` (insert after `ORS_API_KEY=`, line 20)
- Test: `backend/tests/test_email.py` (create), `backend/tests/test_config.py` (append)

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `Settings.smtp_host: str = ""`, `smtp_port: int = 587`, `smtp_username: str = ""`, `smtp_password: str = ""`, `smtp_from_address: str = ""`, `smtp_use_tls: bool = True`, `escalation_email_to: str = "CIHE241731@student.edu.cihe.au"`.
  - `app.integrations.email.send_escalation_email(*, customer_name: str, customer_email: str, question: str, answer: str, support_ticket_id: int) -> None` — Task 5 calls this by keyword.
  - Exceptions: `EmailError(RuntimeError)`; `EmailNotConfiguredError(EmailError)` (empty host / from / to — raised before any connection); `EmailDeliveryError(EmailError)` (wraps `smtplib.SMTPException`/`OSError`, `__cause__` set, underlying message preserved in `str()`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config.py`:

```python
def test_settings_has_optional_smtp_fields_with_spec_defaults():
    fields = Settings.model_fields
    assert fields["smtp_host"].default == ""
    assert fields["smtp_port"].default == 587
    assert fields["smtp_username"].default == ""
    assert fields["smtp_password"].default == ""
    assert fields["smtp_from_address"].default == ""
    assert fields["smtp_use_tls"].default is True
    assert fields["escalation_email_to"].default == "CIHE241731@student.edu.cihe.au"
```

Create `backend/tests/test_email.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_email.py tests/test_config.py -v`

Expected: `tests/test_email.py` fails at collection with `ModuleNotFoundError: No module named 'app.integrations.email'` (all 13 tests error). In `tests/test_config.py`, `test_settings_has_optional_smtp_fields_with_spec_defaults` fails with `KeyError: 'smtp_host'`; the 5 pre-existing tests still pass.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/config.py`, insert immediately after the line `    ors_api_key: str = ""`:

```python
    # Opt-in chat escalation email (app/integrations/email.py; see
    # docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).
    # Every field is optional with an empty/safe default, for the same reason
    # as ors_api_key above: importing app.config must never break for code
    # paths (and tests) that don't send email. An empty smtp_host means "not
    # configured" -- send_escalation_email raises immediately instead of
    # attempting a connection, and POST /chat/messages/{id}/escalate reports
    # email_sent=false while still creating the ticket. smtp_port 587 +
    # smtp_use_tls=True is the standard STARTTLS submission setup.
    # smtp_from_address falls back to smtp_username when empty. smtp_password
    # is only ever passed to smtplib's login() -- never logged.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    smtp_use_tls: bool = True
    escalation_email_to: str = "CIHE241731@student.edu.cihe.au"
```

Create `backend/app/integrations/email.py`:

```python
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
```

In `docker-compose.yml`, insert immediately after the line `      ORS_API_KEY: ${ORS_API_KEY:-}` (same indentation):

```yaml
      # Opt-in chat escalation email (app/integrations/email.py). All
      # optional: an empty SMTP_HOST is treated as "not configured" -- the
      # escalation ticket is still created, only the email is skipped
      # (email_sent=false). Listed here for the same reason as ORS_API_KEY
      # above: a var missing from this block never reaches the container.
      SMTP_HOST: ${SMTP_HOST:-}
      SMTP_PORT: ${SMTP_PORT:-587}
      SMTP_USERNAME: ${SMTP_USERNAME:-}
      SMTP_PASSWORD: ${SMTP_PASSWORD:-}
      SMTP_FROM_ADDRESS: ${SMTP_FROM_ADDRESS:-}
      SMTP_USE_TLS: ${SMTP_USE_TLS:-true}
      ESCALATION_EMAIL_TO: ${ESCALATION_EMAIL_TO:-CIHE241731@student.edu.cihe.au}
```

In the root `.env.example`, insert immediately after the line `ORS_API_KEY=` (keep the blank line that follows it):

```
# Opt-in chat escalation email (app/integrations/email.py). When a customer
# accepts the chat widget's "escalate this to a human?" offer, a support
# ticket is always created; an email is ALSO sent to ESCALATION_EMAIL_TO if
# SMTP_HOST is set. Leave SMTP_HOST blank to run without email (the API then
# reports email_sent=false). SMTP_USE_TLS=true means STARTTLS on SMTP_PORT
# (587 is the standard submission port). SMTP_FROM_ADDRESS falls back to
# SMTP_USERNAME when blank. Never commit a real SMTP_PASSWORD.
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=
SMTP_USE_TLS=true
ESCALATION_EMAIL_TO=CIHE241731@student.edu.cihe.au
```

In `backend/.env.example`, insert immediately after the line `ORS_API_KEY=`:

```
# Opt-in chat escalation email (app/integrations/email.py). Leave SMTP_HOST
# blank to run without email -- escalation tickets are still created, and
# POST /chat/messages/{id}/escalate reports email_sent=false.
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=
SMTP_USE_TLS=true
ESCALATION_EMAIL_TO=CIHE241731@student.edu.cihe.au
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_email.py tests/test_config.py -v`

Expected: all 13 tests in `test_email.py` pass and all 6 tests in `test_config.py` pass (5 pre-existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/integrations/email.py backend/app/config.py docker-compose.yml .env.example backend/.env.example backend/tests/test_email.py backend/tests/test_config.py
git commit -m "Add stdlib SMTP escalation email client and optional SMTP settings"
```

---

## Task 2: Widen the "today's routes" intent keywords

**Dispatch:** Mechanical — transcription only (small logic change, high test count). A fast/cheap implementer is sufficient. Reviewer: the `"deviation"` guard is a deliberate spec-gap resolution (see "Spec gaps" item 3 above), not scope creep.

**Files:**
- Modify: `backend/app/api/chat.py` (comment block + `_TODAYS_ROUTES_*` constants + `_detect_todays_routes_intent`, lines 304-331)
- Test: `backend/tests/test_chat_api.py` (append at end of file)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `_detect_todays_routes_intent(query: str) -> bool` (signature unchanged); new module constant `_TODAYS_ROUTES_EXCLUDED_WORDS = ("deviation",)`. No later task depends on these.

- [ ] **Step 1: Write the failing test**

Append to the end of `backend/tests/test_chat_api.py` (everything it uses — `patch`, `ANY`, `pytest`, `fleet_a`, `client` — is already imported/defined in the file):

```python
# ---------------------------------------------------------------------------
# demo-routes-and-escalation: widened "today's routes" signal words
# ("problem"/"problems"/"issue"/"issues"/"overview") plus false-positive
# regression tests, mirroring the "reported"/"risk" collision fixes above.
# ---------------------------------------------------------------------------

from app.api.chat import _detect_todays_routes_intent  # noqa: E402


@pytest.mark.parametrize(
    "query",
    [
        "what routes were used today?",
        "any active routes today?",
        "are there any problems with my route?",
        "any issues on my routes?",
        "is there a problem with the route my driver is on",
        "give me a route overview",
        "Routes overview please",
        "Any route issues?",
    ],
)
def test_todays_routes_intent_fires_on_a_route_word_plus_a_signal_word(query):
    assert _detect_todays_routes_intent(query) is True


@pytest.mark.parametrize(
    "query",
    [
        # "problem"/"issue"/"overview" with no route word -- ordinary
        # telematics/KB questions that must still reach RAG.
        "my device has a battery problem",
        "is there an issue with my tracker's signal strength?",
        "give me an overview of my fleet's safety",
        "what problems do harsh braking events cause?",
        # Route-deviation questions are domain vocabulary
        # (DrivingEventType.ROUTE_DEVIATION, the seeded "Route deviation
        # alerts explained" KB article), not questions about planned routes.
        # The first is the collision the original "risk" fix documented; the
        # next two are the same collision reopened by "problem"/"issue", and
        # the last shows the guard also covers the pre-existing "today" word.
        "are route deviations a risk to my fleet?",
        "I have a problem with route deviation alerts",
        "are route deviations an issue for my fleet?",
        "how many route deviations happened today?",
        # A route word alone is not enough.
        "what is a route?",
    ],
)
def test_todays_routes_intent_does_not_fire_without_both_words_or_on_route_deviations(query):
    assert _detect_todays_routes_intent(query) is False


@pytest.mark.parametrize(
    "query",
    [
        "are there any problems with my route?",
        "give me a route overview",
        "any issues on my routes?",
    ],
)
def test_widened_route_questions_route_to_the_todays_routes_summary(client, fleet_a, query):
    with (
        patch(
            "app.api.chat.summarize_todays_routes",
            return_value="1 route(s) planned today (1 active, 0 completed).",
        ) as mock_summary,
        patch("app.ai.chat_service.chat_completion") as mock_chat,
    ):
        response = client.post(
            "/chat",
            json={"query": query, "device_id": fleet_a["device"].device_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "1 route(s) planned today (1 active, 0 completed)."
    # Still scoped to the chat session's own customer -- never fleet-wide.
    mock_summary.assert_called_once_with(ANY, customer_id=fleet_a["customer"].customer_id)
    mock_chat.assert_not_called()


@pytest.mark.parametrize(
    "query",
    [
        "my device has a battery problem",
        "is there an issue with my tracker's signal strength?",
        "I have a problem with route deviation alerts",
    ],
)
def test_problem_and_issue_questions_without_a_planned_route_meaning_fall_through_to_rag(
    client, fleet_a, query
):
    with (
        patch("app.api.chat.summarize_todays_routes") as mock_summary,
        patch(
            "app.ai.chat_service.chat_completion",
            return_value='{"answer": "Checking that for you now.", "confidence": 0.8}',
        ) as mock_chat,
    ):
        response = client.post(
            "/chat",
            json={"query": query, "device_id": fleet_a["device"].device_id},
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    mock_summary.assert_not_called()
    mock_chat.assert_called_once()


def test_route_plan_intent_still_wins_over_a_widened_signal_word(client, fleet_a):
    """"route to Z" is a request to plan a NEW route, checked before the
    today's-routes intent -- the widened word "problems" must not steal it."""
    with patch("app.api.chat.summarize_todays_routes") as mock_summary:
        response = client.post(
            "/chat",
            json={
                "query": "any problems on the route to Parramatta",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "Which starting point should I plan this route from?"
    mock_summary.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_chat_api.py -v -k "todays_routes_intent_fires or todays_routes_intent_does_not_fire or widened or problem_and_issue or still_wins"`

Expected: 10 failures. In `test_todays_routes_intent_fires_on_a_route_word_plus_a_signal_word`, the 6 cases using "problem(s)"/"issue(s)"/"overview" fail with `assert False is True` (the two "today"/"active" cases pass). In `test_todays_routes_intent_does_not_fire_...`, only `"how many route deviations happened today?"` fails (`assert True is False`). All 3 cases of `test_widened_route_questions_route_to_the_todays_routes_summary` fail (`mock_summary` not called — the query falls through to RAG). The 3 fall-through cases and the precedence test already pass (they are regression guards). All pre-existing tests in the file still pass.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/api/chat.py`, replace the whole block from the comment line `# Requires BOTH a route-word and a temporal/status word, so an ordinary` through the end of `_detect_todays_routes_intent` (the line `    return has_route_word and has_signal_word`) with:

```python
# Requires BOTH a route-word and a signal word, so an ordinary
# telematics/KB question that happens to mention "route" doesn't get
# swallowed here. Bare "risk"/"risks" are deliberately excluded from the
# signal words since this app has real domain vocabulary built on "risk"
# (DrivingEventType.ROUTE_DEVIATION, risk-related KB content) that would
# otherwise collide with this intent. This also doesn't collide with
# _detect_route_plan_intent's "plan a NEW route" phrasing, which is checked
# first (see post_chat below).
#
# "problem(s)"/"issue(s)"/"overview" were added so "are there any problems
# with my route?" and "give me a route overview" reach this intent (see
# docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).
# Those words reopen exactly the collision the "risk" exclusion closed --
# "are route deviations an issue for my fleet?" contains "route" + "issue" --
# so any query mentioning "deviation" is excluded outright: a route
# deviation is a driving-event concept with its own KB article (it should
# reach RAG), never a question about already-planned routes.
_TODAYS_ROUTES_ROUTE_WORDS = ("route", "routes")
_TODAYS_ROUTES_TEMPORAL_STATUS_WORDS = (
    "today",
    "active",
    "problem",
    "problems",
    "issue",
    "issues",
    "overview",
)
_TODAYS_ROUTES_EXCLUDED_WORDS = ("deviation",)


def _detect_todays_routes_intent(query: str) -> bool:
    """True if the query is asking about already-planned routes (e.g.
    "what routes were used today", "active routes", "any problems with my
    route?", "route overview") rather than asking to plan a NEW route or
    ask an unrelated question that happens to mention "route". Deliberately
    simple keyword matching, same philosophy as
    _detect_report_intent/_detect_route_plan_intent."""
    lowered = query.lower()
    if any(word in lowered for word in _TODAYS_ROUTES_EXCLUDED_WORDS):
        return False
    has_route_word = any(word in lowered for word in _TODAYS_ROUTES_ROUTE_WORDS)
    has_signal_word = any(word in lowered for word in _TODAYS_ROUTES_TEMPORAL_STATUS_WORDS)
    return has_route_word and has_signal_word
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_chat_api.py -v`

Expected: the whole file passes, including all 24 new parametrized cases/tests (8 + 9 + 3 + 3 + 1).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/chat.py backend/tests/test_chat_api.py
git commit -m "Widen today's-routes chat intent to problem/issue/overview questions"
```

---

## Task 3: Driver names in the chat route summary and report route-risk lines

**Dispatch:** Judgment (standard model). Two different call sites with two different lookup strategies; the reviewer should confirm the chat summary issues exactly one batched `Driver` query and that neither path can surface another customer's driver.

**Files:**
- Modify: `backend/app/ai/route_planning.py` (imports lines 30-32; insert helper before and rewrite `summarize_todays_routes`, lines 472-518)
- Modify: `backend/app/ai/reports.py` (imports lines 80-87; `_format_route_risk_warnings` lines 239-262; call sites at lines 376 and 416)
- Test: `backend/tests/test_summarize_todays_routes.py` (create), `backend/tests/test_reports.py` (import lines 30 and 35; append)

**Interfaces:**
- Consumes: `RoutePlan.driver_id` (existing column), `Driver.full_name` (existing).
- Produces:
  - `app.ai.route_planning.route_plan_driver_label(driver_id: int | None, drivers_by_id: dict[int, Driver]) -> str` — `"Unassigned"` for `None`, the driver's `full_name` when found, `f"driver {driver_id}"` otherwise.
  - `summarize_todays_routes(db, *, customer_id)` (signature unchanged) — each route line is now `f"- {origin} -> {destination} ({status}, driver: {label})"` followed by the unchanged `": ..."` suffix. Task 7's test reads this.
  - `app.ai.reports._format_route_risk_warnings(route_plans: list[RoutePlan], drivers_by_id: dict[int, Driver]) -> str` — each flagged route line is now `f"  - {origin} -> {destination} (driver: {label}): N warning(s)..."`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_summarize_todays_routes.py`:

```python
"""Tests for the driver names added to
`app.ai.route_planning.summarize_todays_routes` (the chat "today's routes"
answer) -- see docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md,
"Driver names in existing output".

Direct function tests against an in-memory SQLite session: no FastAPI, and
no LLM (summarize_todays_routes never calls one). Rows are created with the
default `created_at` (now), which always falls inside today's Sydney-local
window that summarize_todays_routes filters on.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import route_plan_driver_label, summarize_todays_routes
from app.auth.security import hash_password
from app.models import Base, Customer, Driver, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Summary Customer {tag}",
        email=f"summary-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_driver(db_session: Session, *, customer: Customer, full_name: str, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=full_name,
        license_number=f"LIC-SUMMARY-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def _make_route_plan(
    db_session: Session,
    *,
    customer: Customer,
    origin: str = "Sydney CBD",
    destination: str = "Parramatta",
    driver_id: int | None = None,
    status: RoutePlanStatus = RoutePlanStatus.ACTIVE,
    warnings: list | None = None,
    unavailable: bool = False,
) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        driver_id=driver_id,
        origin_label=origin,
        destination_label=destination,
        warnings=warnings or [],
        unavailable=unavailable,
        status=status,
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def _capture_statements(engine):
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    return statements, lambda: event.remove(engine, "before_cursor_execute", _capture)


def test_route_line_names_the_assigned_driver(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    _make_route_plan(
        db_session,
        customer=customer,
        driver_id=driver.driver_id,
        warnings=[
            {"type": "risk_zone", "severity": "high", "description": "x"},
            {"type": "weather", "severity": "moderate", "description": "y"},
        ],
    )

    summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)

    assert (
        "- Sydney CBD -> Parramatta (active, driver: Alice Driver): 2 warning(s), 1 high-severity"
        in summary
    )


def test_unassigned_route_line_says_unassigned(db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(
        db_session,
        customer=customer,
        origin="Bondi Beach",
        destination="Sydney Airport",
        status=RoutePlanStatus.COMPLETED,
    )

    summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)

    assert "- Bondi Beach -> Sydney Airport (completed, driver: Unassigned): no warnings" in summary


def test_unavailable_route_line_still_names_the_driver(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Bob Driver", tag="B")
    _make_route_plan(db_session, customer=customer, driver_id=driver.driver_id, unavailable=True)

    summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)

    assert (
        "- Sydney CBD -> Parramatta (active, driver: Bob Driver): "
        "route data was unavailable when planned" in summary
    )


def test_drivers_are_fetched_in_one_batched_query_not_per_row(engine, db_session):
    customer = _make_customer(db_session, tag="A")
    alice = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    bob = _make_driver(db_session, customer=customer, full_name="Bob Driver", tag="B")
    _make_route_plan(db_session, customer=customer, driver_id=alice.driver_id)
    _make_route_plan(db_session, customer=customer, driver_id=bob.driver_id)
    _make_route_plan(db_session, customer=customer, driver_id=alice.driver_id)
    _make_route_plan(db_session, customer=customer)

    statements, stop = _capture_statements(engine)
    try:
        summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)
    finally:
        stop()

    assert len([s for s in statements if "FROM drivers" in s]) == 1
    assert summary.count("driver: Alice Driver") == 2
    assert summary.count("driver: Bob Driver") == 1
    assert summary.count("driver: Unassigned") == 1


def test_no_driver_query_when_every_route_is_unassigned(engine, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)

    statements, stop = _capture_statements(engine)
    try:
        summarize_todays_routes(db_session, customer_id=customer.customer_id)
    finally:
        stop()

    assert [s for s in statements if "FROM drivers" in s] == []


def test_route_plan_driver_label_covers_unassigned_known_and_unknown_drivers():
    driver = Driver(driver_id=7, customer_id=1, full_name="Alice Driver", license_number="LIC-X")

    assert route_plan_driver_label(None, {}) == "Unassigned"
    assert route_plan_driver_label(7, {7: driver}) == "Alice Driver"
    assert route_plan_driver_label(42, {7: driver}) == "driver 42"
```

In `backend/tests/test_reports.py`, change line 30 from:

```python
from app.ai.reports import generate_end_of_day_report, generate_start_of_day_report
```

to:

```python
from app.ai.reports import (
    _format_route_risk_warnings,
    generate_end_of_day_report,
    generate_start_of_day_report,
)
```

and change line 35 from:

```python
from app.models import Base, Customer, Device, Driver, DrivingEvent, SupportAgent, Trip, Vehicle
```

to:

```python
from app.models import (
    Base,
    Customer,
    Device,
    Driver,
    DrivingEvent,
    RoutePlan,
    SupportAgent,
    Trip,
    Vehicle,
)
```

Then append to the end of `backend/tests/test_reports.py`:

```python
# ---------------------------------------------------------------------------
# demo-routes-and-escalation: route-risk warning lines name the driver
# ---------------------------------------------------------------------------

_HIGH_RISK_WARNING = {
    "location": {"lat": -33.85, "lon": 151.1},
    "distance_from_origin_km": 10.0,
    "type": "risk_zone",
    "severity": "high",
    "description": "16 driving events recorded within 500m of this point (7 harsh braking).",
}


def _transient_route_plan(*, driver_id: int | None, warnings: list) -> RoutePlan:
    """Never added to a session -- `_format_route_risk_warnings` only reads
    attributes, so an in-memory row is enough for a pure formatter test."""
    return RoutePlan(
        customer_id=1,
        created_by_role="support_agent",
        created_by_id=1,
        driver_id=driver_id,
        origin_label="Sydney CBD",
        destination_label="Parramatta",
        warnings=warnings,
        unavailable=False,
    )


def test_route_risk_warning_line_names_the_assigned_driver():
    driver = Driver(driver_id=7, customer_id=1, full_name="Alice Driver", license_number="LIC-X")

    text = _format_route_risk_warnings(
        [_transient_route_plan(driver_id=7, warnings=[_HIGH_RISK_WARNING])], {7: driver}
    )

    assert "  - Sydney CBD -> Parramatta (driver: Alice Driver): 1 warning(s), 1 high-severity" in text


def test_route_risk_warning_line_says_unassigned_when_no_driver():
    text = _format_route_risk_warnings(
        [_transient_route_plan(driver_id=None, warnings=[_HIGH_RISK_WARNING])], {}
    )

    assert "  - Sydney CBD -> Parramatta (driver: Unassigned): 1 warning(s), 1 high-severity" in text


def _add_flagged_route_plan(db_session, fleet) -> None:
    db_session.add(
        RoutePlan(
            customer_id=fleet["customer"].customer_id,
            created_by_role="customer",
            created_by_id=fleet["customer"].customer_id,
            driver_id=fleet["driver1"].driver_id,
            origin_label="Sydney CBD",
            destination_label="Parramatta",
            warnings=[_HIGH_RISK_WARNING],
            unavailable=False,
            created_at=_NOW,
        )
    )
    db_session.commit()


@pytest.mark.parametrize("generator", [generate_start_of_day_report, generate_end_of_day_report])
def test_both_reports_name_the_driver_on_a_flagged_route(db_session, fleet_a, generator):
    _add_flagged_route_plan(db_session, fleet_a)

    with patch("app.ai.reports.chat_completion", return_value="summary") as mock_chat:
        generator(
            fleet_a["customer"].customer_id,
            db=db_session,
            data_source=SyntheticDataSource(db_session),
            now=_NOW,
        )

    context = mock_chat.call_args[0][0][1]["content"]
    assert (
        f"  - Sydney CBD -> Parramatta (driver: {fleet_a['driver1'].full_name}): "
        "1 warning(s), 1 high-severity"
    ) in context
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_summarize_todays_routes.py tests/test_reports.py -v`

Expected: `tests/test_summarize_todays_routes.py` fails at collection with `ImportError: cannot import name 'route_plan_driver_label' from 'app.ai.route_planning'` (all 6 tests error). In `tests/test_reports.py`, the two formatter tests fail with `TypeError: _format_route_risk_warnings() takes 1 positional argument but 2 were given`, and both parametrized report cases fail on the `in context` assertion (the line reads `  - Sydney CBD -> Parramatta: 1 warning(s), ...` with no driver). All pre-existing `test_reports.py` tests still pass.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/ai/route_planning.py`, add this import immediately after the line `from app.models.route_plan import RoutePlan`:

```python
from app.models.telematics import Driver
```

Replace the whole `summarize_todays_routes` function (from `def summarize_todays_routes(db: Session, *, customer_id: int | None) -> str:` to the end of the file) with:

```python
def route_plan_driver_label(driver_id: int | None, drivers_by_id: dict[int, Driver]) -> str:
    """Who was driving a route plan, for a human-readable summary line:
    "Unassigned" when no driver was set (`RoutePlan.driver_id` is optional),
    the driver's `full_name` when found in `drivers_by_id`, or
    `"driver <id>"` if the id isn't in the map (mirrors
    `app.ai.reports._format_planned_routes`' own fallback). Shared by
    `summarize_todays_routes` below and `app.ai.reports`'s route-risk
    section so both surfaces label drivers identically."""
    if driver_id is None:
        return "Unassigned"
    driver = drivers_by_id.get(driver_id)
    return driver.full_name if driver is not None else f"driver {driver_id}"


def summarize_todays_routes(db: Session, *, customer_id: int | None) -> str:
    """Deterministic (no LLM call) summary of today's RoutePlan rows, for
    the chat "today's routes" intent (app/api/chat.py's
    _detect_todays_routes_intent). customer_id=None means fleet-wide (a
    support_agent asking without narrowing to one customer) -- mirrors
    every other support_agent-facing list endpoint's unscoped-means-all
    convention (see GET /route-plans, GET /tickets). NOTE: the chat intent
    itself always passes the chat session's own customer_id (see the
    SECURITY FIX comment in app/api/chat.py); this function's None branch
    is never reached from chat.

    Each route line names who was driving it ("driver: Alice Driver", or
    "driver: Unassigned"). Driver names are fetched with ONE batched
    `Driver.driver_id IN (...)` query per call -- never one query per row --
    and skipped entirely when no route has a driver. The ids come only from
    the already customer-scoped route rows above, so this lookup can never
    surface a driver outside that scope.

    Deliberately does NOT call the LLM: the numbers here (warning counts,
    severities) come straight from stored RoutePlan rows, and rewriting
    them through an LLM risks exactly the kind of drift/hallucination this
    app's RAG pipeline already guards against elsewhere (see
    app/ai/chat_service.py's strict "answer only from context" system
    prompt)."""
    start, end = site_day_bounds(site_today())
    query = db.query(RoutePlan).filter(
        RoutePlan.created_at >= start, RoutePlan.created_at < end
    )
    if customer_id is not None:
        query = query.filter(RoutePlan.customer_id == customer_id)
    routes = query.order_by(RoutePlan.created_at.desc()).all()

    if not routes:
        return "No routes have been planned today."

    driver_ids = sorted({r.driver_id for r in routes if r.driver_id is not None})
    drivers_by_id: dict[int, Driver] = (
        {
            driver.driver_id: driver
            for driver in db.query(Driver).filter(Driver.driver_id.in_(driver_ids)).all()
        }
        if driver_ids
        else {}
    )

    active_count = sum(1 for r in routes if r.status == RoutePlanStatus.ACTIVE)
    completed_count = len(routes) - active_count

    lines = [
        f"{len(routes)} route(s) planned today "
        f"({active_count} active, {completed_count} completed)."
    ]
    for route in routes:
        status_label = "active" if route.status == RoutePlanStatus.ACTIVE else "completed"
        driver_label = route_plan_driver_label(route.driver_id, drivers_by_id)
        detail = (
            f"- {route.origin_label} -> {route.destination_label} "
            f"({status_label}, driver: {driver_label})"
        )
        if route.unavailable:
            detail += ": route data was unavailable when planned"
        elif route.warnings:
            high_severity = sum(1 for w in route.warnings if w.get("severity") == "high")
            detail += f": {len(route.warnings)} warning(s)"
            if high_severity:
                detail += f", {high_severity} high-severity"
        else:
            detail += ": no warnings"
        lines.append(detail)

    return "\n".join(lines)
```

In `backend/app/ai/reports.py`, add this import immediately after the line `from app.ai.llm import chat_completion`:

```python
from app.ai.route_planning import route_plan_driver_label
```

Replace the whole `_format_route_risk_warnings` function with:

```python
def _format_route_risk_warnings(
    route_plans: list[RoutePlan], drivers_by_id: dict[int, Driver]
) -> str:
    """Weather/risk-zone warnings recorded against today's planned routes
    (see `app.ai.route_planning.build_route_plan`'s `Warning` type) -- the
    one report section sourced from the route-planning feature's own risk
    signals, rather than raw `DrivingEvent` counts.

    Each flagged route's line names who was driving it (or "Unassigned"),
    looked up in `drivers_by_id` -- the SAME per-customer map the calling
    report generator already batch-fetched once via
    `ds.list_drivers(customer_id)` for its other sections, so naming drivers
    here costs no extra query. That map covers every driver a route can
    carry: `RoutePlan.driver_id` is validated against the plan's own
    customer's fleet on write (`_resolve_driver_id` in
    app/api/route_plan.py), and `route_plans` is already filtered to that
    same customer (`_route_plans_for_today`)."""
    if not route_plans:
        return "(no routes planned today)"
    flagged = [rp for rp in route_plans if rp.warnings]
    if not flagged:
        return f"No risk/weather warnings on any of {len(route_plans)} route(s) planned today."
    lines = [f"{len(flagged)} of {len(route_plans)} route(s) planned today carry warnings:"]
    for route_plan in flagged:
        high_severity = sum(1 for w in route_plan.warnings if w.get("severity") == "high")
        driver_label = route_plan_driver_label(route_plan.driver_id, drivers_by_id)
        lines.append(
            f"  - {route_plan.origin_label} -> {route_plan.destination_label} "
            f"(driver: {driver_label}): "
            f"{len(route_plan.warnings)} warning(s)"
            f"{f', {high_severity} high-severity' if high_severity else ''}"
        )
        for warning in route_plan.warnings:
            lines.append(
                f"      * {warning.get('type', 'warning')} "
                f"({warning.get('severity', 'unknown')}): {warning.get('description', '')}"
            )
    return "\n".join(lines)
```

In `generate_start_of_day_report`, change the last line of its `context_block` from:

```python
        f"{_format_route_risk_warnings(todays_route_plans)}"
```

to:

```python
        f"{_format_route_risk_warnings(todays_route_plans, drivers_by_id)}"
```

and make the identical change to the last line of `generate_end_of_day_report`'s `context_block` (both functions already define `drivers_by_id` above that point).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_summarize_todays_routes.py tests/test_reports.py tests/test_chat_api.py -v`

Expected: all 6 tests in `test_summarize_todays_routes.py` pass; every test in `test_reports.py` passes (pre-existing + 4 new: 2 formatter tests + 2 parametrized report cases); `test_chat_api.py` still passes in full (`test_todays_routes_intent_reports_saved_routes` asserts substrings `"Sydney CBD -> Parramatta"` and `"1 high-severity"`, both still present).

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/route_planning.py backend/app/ai/reports.py backend/tests/test_summarize_todays_routes.py backend/tests/test_reports.py
git commit -m "Name the driver on each route line in chat summaries and reports"
```

---

## Task 4: Opt-in escalation — `EscalationResult.escalation_offered`, `handle_answer` stops creating tickets

**Dispatch:** Judgment (most capable model). This changes existing, tested behavior. The implementer must update every existing assertion on `escalated` for the low-confidence path — listed exhaustively below (found by `grep -rn "escalated\|handle_answer\|_get_or_create_escalation_ticket" backend`) — without weakening anything else. Reviewer: confirm no test that previously asserted `escalated is True` for a low-confidence `POST /chat` turn was deleted rather than converted, and that `escalated` is `False` on every `POST /chat` branch.

Every existing caller/test of the changed behavior:
- `backend/app/api/chat.py:487` — the only production caller of `handle_answer`.
- `backend/tests/test_escalation.py` — whole file (rewritten below).
- `backend/tests/test_chat_api.py:681` `test_escalation_path_returns_escalated_true_and_fallback_text` — converted.
- `backend/tests/test_chat_api.py:725` `test_two_consecutive_low_confidence_turns_reuse_the_same_ticket` — converted.
- `backend/tests/test_chat_api.py:865` (inside `test_customer_supplied_cross_tenant_trip_and_driver_ids_do_not_leak`) — one assertion converted.
- `backend/tests/test_chat_api.py:777` `test_failure_after_ticket_and_notification_leaves_no_stranded_rows` — **unchanged**: it asserts zero tickets/sessions/messages after a rollback, which is still true; it still proves the session + message rows roll back together.
- `backend/tests/test_audit.py:271` `test_escalated_chat_answer_audit_log_notes_escalation` — converted.
- `backend/tests/test_feedback_api.py` — **unchanged**: it seeds a pre-existing ticket directly via the ORM and never calls `handle_answer`.
- Assertions of `escalated is False` elsewhere in `test_chat_api.py` (lines 264, 315, 413, 460, 971, 985, 1045) stay true unchanged.

**Files:**
- Modify (replace whole file): `backend/app/ai/escalation.py`
- Modify: `backend/app/api/chat.py` (`ChatResponse` lines 109-124; `post_chat` lines 443-557 — five targeted edits)
- Modify: `backend/app/repositories/chat.py` (module docstring, line 31)
- Modify: `backend/app/security/audit.py` (module docstring, lines 14-16)
- Modify: `.env.example` (root; lines 34-37)
- Test: `backend/tests/test_escalation.py` (replace whole file), `backend/tests/test_chat_api.py` (import block lines 54-65; lines 681-774; lines 862-865; append), `backend/tests/test_audit.py` (lines 271-290)

**Interfaces:**
- Consumes: nothing from Tasks 1-3.
- Produces:
  - `EscalationResult(text: str, escalated: bool, escalation_offered: bool, support_ticket_id: int | None)` (dataclass, field order as written).
  - `handle_answer(chat_answer: ChatAnswer) -> EscalationResult` — no `db`, no `chat_session_id`; never creates rows; always `escalated=False`, `support_ticket_id=None`; `escalation_offered=True` iff `confidence < settings.escalation_confidence_threshold`.
  - `get_or_create_escalation_ticket(db: Session, chat_session: ChatSession, *, description: str, notification_type: PreferredNotificationMethod = PreferredNotificationMethod.IN_APP) -> SupportTicket` — public (was `_get_or_create_escalation_ticket(db, chat_session, chat_answer, *, notification_type)`); select-first, `IntegrityError`-in-SAVEPOINT fallback, one `Notification` only when the ticket is new; flushes, never commits. **Task 5 calls this.**
  - `ChatResponse.escalation_offered: bool` (required, after `escalated`). **Task 6 reads this.**
  - `chat_answer` audit description format: `f"chat_session_id={id} confidence={c:.3f} escalated={e} escalation_offered={o}"`.

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `backend/tests/test_escalation.py` with:

```python
"""Tests for `app.ai.escalation` (Task 14, reworked for opt-in escalation --
docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).

Two halves, matching the module's two functions:
  * `handle_answer` is now a pure decision: high confidence passes the LLM
    text through; low confidence swaps in the exact `FALLBACK_TEXT` and sets
    `escalation_offered=True`. It never creates a ticket -- it no longer
    even takes a `Session` -- and never sets `escalated=True`.
  * `get_or_create_escalation_ticket` (called only by the explicit
    `POST /chat/messages/{id}/escalate` endpoint) is tested against a real
    in-memory SQLite round-trip, same fixture pattern as
    `test_chat_repository.py`: exactly one ticket + one in-app notification,
    reuse instead of `IntegrityError` on a second call, and the
    SAVEPOINT-scoped recovery for the concurrent race (final-review Fix 6),
    which must not roll back unrelated work staged earlier in the same
    outer transaction.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer
from app.ai.escalation import EscalationResult, get_or_create_escalation_ticket, handle_answer
from app.config import settings
from app.models import Base, ChatSession, Customer, Device, Notification, SupportTicket
from app.models.enums import DeviceStatus, PreferredNotificationMethod
from app.repositories.chat import create_chat_session, create_support_ticket


@pytest.fixture()
def engine():
    """An in-memory SQLite engine, shared across connections, with FK enforcement on."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def session(engine):
    with Session(engine) as db_session:
        yield db_session


def _make_customer_and_device(session, suffix: str) -> tuple[Customer, Device]:
    customer = Customer(
        full_name=f"Escalation Customer {suffix}",
        email=f"escalation{suffix}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash="hashed",
    )
    session.add(customer)
    session.flush()
    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"SN-ESC-{suffix}",
        device_type="obd-ii",
        device_status=DeviceStatus.ACTIVE,
    )
    session.add(device)
    session.flush()
    return customer, device


def _make_chat_session(session, suffix: str) -> ChatSession:
    customer, device = _make_customer_and_device(session, suffix)
    return create_chat_session(session, customer_id=customer.customer_id, device_id=device.device_id)


def _tickets_for_session(session, chat_session_id: int) -> list[SupportTicket]:
    return list(
        session.execute(
            select(SupportTicket).where(SupportTicket.chat_session_id == chat_session_id)
        )
        .scalars()
        .all()
    )


def _notifications_for_ticket(session, support_ticket_id: int) -> list[Notification]:
    return list(
        session.execute(
            select(Notification).where(Notification.support_ticket_id == support_ticket_id)
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# handle_answer: a pure offer/no-offer decision
# ---------------------------------------------------------------------------


def test_high_confidence_answer_passes_through_and_offers_nothing():
    answer = ChatAnswer(text="Your device battery is at 80%.", confidence=0.9)
    assert answer.confidence >= settings.escalation_confidence_threshold

    result = handle_answer(answer)

    assert isinstance(result, EscalationResult)
    assert result.text == "Your device battery is at 80%."
    assert result.escalated is False
    assert result.escalation_offered is False
    assert result.support_ticket_id is None


def test_confidence_exactly_at_threshold_does_not_offer_escalation():
    """'below threshold' offers; exactly-at-threshold must NOT (direction check)."""
    answer = ChatAnswer(text="Exactly at the line.", confidence=settings.escalation_confidence_threshold)

    result = handle_answer(answer)

    assert result.escalation_offered is False
    assert result.text == "Exactly at the line."


def test_low_confidence_answer_offers_escalation_with_the_exact_fallback_text():
    answer = ChatAnswer(text="I think maybe the device is possibly offline?", confidence=0.1)
    assert answer.confidence < settings.escalation_confidence_threshold

    result = handle_answer(answer)

    assert result.text is FALLBACK_TEXT  # exact constant reused, not retyped
    assert "possibly offline" not in result.text
    assert result.escalation_offered is True
    # Offered, NOT escalated: nothing exists yet until the customer says yes.
    assert result.escalated is False
    assert result.support_ticket_id is None


def test_handle_answer_cannot_create_database_rows():
    """Opt-in guard: `handle_answer` takes no `Session`, so it has no way to
    create a ticket implicitly. Re-adding a `db` parameter would be the
    first step back toward silent auto-escalation -- this test should fail
    loudly if that happens."""
    assert list(inspect.signature(handle_answer).parameters) == ["chat_answer"]


# ---------------------------------------------------------------------------
# get_or_create_escalation_ticket: the explicit-confirm path's ticket helper
# ---------------------------------------------------------------------------


def test_creates_exactly_one_ticket_and_one_in_app_notification(session):
    chat_session = _make_chat_session(session, "GC1")

    ticket = get_or_create_escalation_ticket(
        session,
        chat_session,
        description="Customer question: why?\n\nAssistant answer: fallback",
    )
    session.commit()

    session.expire_all()
    tickets = _tickets_for_session(session, chat_session.chat_session_id)
    assert len(tickets) == 1
    assert tickets[0].support_ticket_id == ticket.support_ticket_id
    assert tickets[0].customer_id == chat_session.customer_id
    assert tickets[0].device_id == chat_session.device_id
    assert tickets[0].subject
    assert tickets[0].description == "Customer question: why?\n\nAssistant answer: fallback"

    notifications = _notifications_for_ticket(session, ticket.support_ticket_id)
    assert len(notifications) == 1
    assert notifications[0].customer_id == chat_session.customer_id
    assert notifications[0].notification_type == PreferredNotificationMethod.IN_APP
    assert notifications[0].message


def test_second_call_reuses_the_ticket_without_a_second_notification(session):
    """Double-click safety: `ChatSession 1 -> 0..1 SupportTicket` is a DB
    UNIQUE constraint, so a second call must select-and-reuse, not raise
    `IntegrityError` -- and must not notify the customer twice."""
    chat_session = _make_chat_session(session, "GC2")

    first = get_or_create_escalation_ticket(session, chat_session, description="first")
    second = get_or_create_escalation_ticket(session, chat_session, description="second")
    session.commit()

    assert second.support_ticket_id == first.support_ticket_id
    session.expire_all()
    assert len(_tickets_for_session(session, chat_session.chat_session_id)) == 1
    assert len(_notifications_for_ticket(session, first.support_ticket_id)) == 1


def test_concurrent_ticket_race_recovers_via_savepoint_without_losing_other_staged_work(
    session, monkeypatch
):
    """Defense-in-depth path (final-review Fix 6 interaction): simulates the
    genuinely concurrent race two simultaneous "Yes" clicks for the same
    session could hit -- the initial SELECT misses a ticket a "concurrent"
    insert already created, so `create_support_ticket`'s INSERT hits the
    real UNIQUE constraint.

    `get_or_create_escalation_ticket` must recover via its except-block
    re-select (finding the real, already-flushed ticket) AND must NOT roll
    back unrelated work staged earlier in the same outer transaction --
    proving the `db.begin_nested()` SAVEPOINT scopes the rollback to only
    the failed insert, not the whole transaction.
    """
    chat_session = _make_chat_session(session, "RACE1")
    # Stand-in for other work staged earlier in the SAME outer transaction.
    other_chat_session = _make_chat_session(session, "RACE1-OTHER")

    # Stand-in for "a concurrent request already won the race".
    winning_ticket = create_support_ticket(
        session,
        chat_session_id=chat_session.chat_session_id,
        customer_id=chat_session.customer_id,
        device_id=chat_session.device_id,
        subject="Concurrent request's ticket",
    )

    # Force exactly the NEXT `.one_or_none()` call -- the helper's own
    # initial select-first check -- to report "nothing found".
    original_one_or_none = SAQuery.one_or_none
    state = {"missed_once": False}

    def _miss_once(self):
        if not state["missed_once"]:
            state["missed_once"] = True
            return None
        return original_one_or_none(self)

    monkeypatch.setattr(SAQuery, "one_or_none", _miss_once)

    ticket = get_or_create_escalation_ticket(
        session, chat_session, description="a low-confidence answer"
    )

    assert ticket.support_ticket_id == winning_ticket.support_ticket_id

    session.expire_all()
    assert session.get(ChatSession, other_chat_session.chat_session_id) is not None
```

In `backend/tests/test_chat_api.py`, change the `from app.models import (` block (lines 54-65) so it also imports `Notification`, i.e. it reads:

```python
from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Customer,
    Device,
    Driver,
    Notification,
    RoutePlan,
    SupportTicket,
    Trip,
    Vehicle,
)
```

Replace the two tests `test_escalation_path_returns_escalated_true_and_fallback_text` and `test_two_consecutive_low_confidence_turns_reuse_the_same_ticket` (lines 681-774, i.e. everything from `def test_escalation_path_returns_escalated_true_and_fallback_text(` up to, not including, `def test_failure_after_ticket_and_notification_leaves_no_stranded_rows(`) with:

```python
def test_low_confidence_answer_offers_escalation_without_creating_a_ticket(
    client, db_session, fleet_a
):
    """Opt-in escalation (docs/superpowers/specs/2026-09-30-demo-routes-and-
    escalation-design.md): a low-confidence answer still shows the exact
    FALLBACK_TEXT, but no longer creates a SupportTicket/Notification on its
    own -- it only sets `escalation_offered`, and the customer decides via
    POST /chat/messages/{id}/escalate.

    No trip/driver/vehicle context resolved and no KB articles seeded, so
    `_compute_confidence` pins confidence to the 0.1 floor -- below
    `settings.escalation_confidence_threshold` (0.6 default) -- regardless
    of what the (mocked) LLM says."""
    assert 0.1 < settings.escalation_confidence_threshold

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="I think it might possibly be a battery issue?",
    ):
        response = client.post(
            "/chat",
            json={
                "query": "why is my device offline?",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["escalation_offered"] is True
    assert body["escalated"] is False
    assert body["answer"] == FALLBACK_TEXT
    assert body["confidence"] <= 0.1

    db_session.expire_all()
    assert db_session.query(SupportTicket).count() == 0
    assert db_session.query(Notification).count() == 0

    # The persisted assistant message is the fallback text, not the raw LLM guess.
    messages = (
        db_session.query(ChatMessage)
        .filter_by(chat_session_id=body["session_id"], role="assistant")
        .all()
    )
    assert messages[0].content == FALLBACK_TEXT


def test_two_consecutive_low_confidence_turns_each_offer_escalation_and_create_no_ticket(
    client, db_session, fleet_a
):
    """Before opt-in, a second low-confidence turn in the same session had
    to reuse the first turn's auto-created ticket (final-review Fix 2). Now
    neither turn creates one: each simply offers escalation again."""
    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="I think it might possibly be a battery issue?",
    ):
        first_response = client.post(
            "/chat",
            json={
                "query": "why is my device offline?",
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )
    assert first_response.status_code == 200
    first_body = first_response.json()
    assert first_body["escalation_offered"] is True
    session_id = first_body["session_id"]

    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Still not sure, maybe try rebooting?",
    ):
        second_response = client.post(
            "/chat",
            json={
                "query": "it's still offline, what now?",
                "session_id": session_id,
            },
            headers=fleet_a["headers"],
        )

    assert second_response.status_code == 200
    second_body = second_response.json()
    assert second_body["escalation_offered"] is True
    assert second_body["escalated"] is False
    assert second_body["answer"] == FALLBACK_TEXT

    db_session.expire_all()
    assert db_session.query(SupportTicket).filter_by(chat_session_id=session_id).count() == 0


```

In `test_customer_supplied_cross_tenant_trip_and_driver_ids_do_not_leak`, replace:

```python
    # Confidence reflects "nothing was found" -- the strongest available
    # signal that no cross-tenant data was silently included.
    assert body["confidence"] <= 0.1
    assert body["escalated"] is True
```

with:

```python
    # Confidence reflects "nothing was found" -- the strongest available
    # signal that no cross-tenant data was silently included.
    assert body["confidence"] <= 0.1
    assert body["escalation_offered"] is True
    assert body["escalated"] is False
```

Append to the end of `backend/tests/test_chat_api.py` (after Task 2's block):

```python
# ---------------------------------------------------------------------------
# demo-routes-and-escalation: escalation is only ever OFFERED on a
# low-confidence RAG turn -- never on a confident answer or on any of the
# always-delivered non-RAG intents.
# ---------------------------------------------------------------------------


def test_confident_and_non_rag_turns_never_offer_escalation(client, db_session, fleet_a):
    _seed_article(db_session)
    with patch("app.ai.chat_service.chat_completion", return_value="Your trip covered 42.5 km."):
        confident = client.post(
            "/chat",
            json={
                "query": "how far was my trip?",
                "trip_id": fleet_a["trip"].trip_id,
                "device_id": fleet_a["device"].device_id,
            },
            headers=fleet_a["headers"],
        )
    with patch("app.api.chat.generate_end_of_day_report", return_value="report text"):
        report = client.post(
            "/chat",
            json={"query": "give me the daily report", "device_id": fleet_a["device"].device_id},
            headers=fleet_a["headers"],
        )
    todays_routes = client.post(
        "/chat",
        json={"query": "what routes were used today?", "device_id": fleet_a["device"].device_id},
        headers=fleet_a["headers"],
    )

    for response in (confident, report, todays_routes):
        assert response.status_code == 200
        assert response.json()["escalation_offered"] is False
        assert response.json()["escalated"] is False
```

In `backend/tests/test_audit.py`, replace the whole `test_escalated_chat_answer_audit_log_notes_escalation` function (lines 271-290) with:

```python
def test_low_confidence_chat_answer_audit_log_notes_the_escalation_offer(
    client, db_session, fleet
):
    """Low-confidence (empty-context) answers are now only OFFERED
    escalation (opt-in) -- the audit entry must record
    `escalation_offered=True` and `escalated=False`, not a stale/default
    value."""
    with patch(
        "app.ai.chat_service.chat_completion",
        return_value="Generic answer with no grounding at all.",
    ):
        response = client.post(
            "/chat",
            json={"query": "totally unrelated question", "device_id": fleet["device"].device_id},
            headers=fleet["headers"],
        )
    assert response.status_code == 200
    body = response.json()
    assert body["escalation_offered"] is True
    assert body["escalated"] is False

    db_session.expire_all()
    entry = db_session.query(AuditLog).filter_by(action=ACTION_CHAT_ANSWER).one()
    assert "escalated=False" in entry.description
    assert "escalation_offered=True" in entry.description
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_escalation.py tests/test_chat_api.py tests/test_audit.py -v`

Expected: `tests/test_escalation.py` fails at collection with `ImportError: cannot import name 'get_or_create_escalation_ticket' from 'app.ai.escalation'` (all 7 tests error). In `test_chat_api.py`, `test_low_confidence_answer_offers_escalation_without_creating_a_ticket`, `test_two_consecutive_low_confidence_turns_each_offer_escalation_and_create_no_ticket`, `test_customer_supplied_cross_tenant_trip_and_driver_ids_do_not_leak` and `test_confident_and_non_rag_turns_never_offer_escalation` fail with `KeyError: 'escalation_offered'`. In `test_audit.py`, `test_low_confidence_chat_answer_audit_log_notes_the_escalation_offer` fails with `KeyError: 'escalation_offered'`. Everything else passes.

- [ ] **Step 3: Write minimal implementation**

Replace the entire contents of `backend/app/ai/escalation.py` with:

```python
"""Escalation logic (Task 14, reworked for opt-in escalation): `handle_answer`
and `get_or_create_escalation_ticket`.

Opt-in, not automatic (docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).
This module used to create a `SupportTicket` + `Notification` silently
whenever an answer's confidence fell below the threshold. It no longer does.
There are now two separate steps with two separate callers:

  1. `handle_answer` -- called by `POST /chat` for every RAG turn -- only
     DECIDES. Below `settings.escalation_confidence_threshold` it swaps the
     raw LLM text for Task 13's exact `FALLBACK_TEXT` (the ASS2 compliance
     requirement: an unreliable answer is never shown to the customer; see
     `chat_service.py`'s module docstring) and sets
     `escalation_offered=True`, so the chat widget can ask "Would you like
     to escalate this to a human?". It touches no database row at all --
     which is why it no longer takes a `Session`.
  2. `get_or_create_escalation_ticket` -- called only by
     `POST /chat/messages/{id}/escalate`, i.e. only after the customer
     explicitly said yes -- creates the ticket + in-app notification,
     idempotently.

`EscalationResult.escalated` keeps its meaning ("a ticket now exists for
this exchange"). Since `handle_answer` never creates one, it now always
returns `escalated=False`; nothing implicit ever sets it again.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.chat_service import FALLBACK_TEXT, ChatAnswer
from app.config import settings
from app.models.chat import ChatSession, SupportTicket
from app.models.enums import PreferredNotificationMethod
from app.repositories.chat import create_notification, create_support_ticket

_ESCALATION_TICKET_SUBJECT = (
    "Customer asked for a human after the AI assistant could not confidently answer"
)

#: Message stored on the in-app `Notification` created when a customer
#: accepts the escalation offer.
_ESCALATION_NOTIFICATION_MESSAGE = (
    "You asked for your question to be escalated to a support agent after "
    "the AI assistant could not confidently answer it. A support ticket has "
    "been created and an agent will follow up with you."
)


@dataclass
class EscalationResult:
    """The result of one `handle_answer` call.

    `text` is what should actually be shown to the customer: either the
    original `ChatAnswer.text` (confident) or Task 13's `FALLBACK_TEXT`
    (not confident). `escalation_offered` is True exactly when the fallback
    was used -- the customer should be asked whether to escalate.
    `escalated`/`support_ticket_id` describe a ticket that exists for this
    exchange; `handle_answer` never creates one, so they are always
    `False`/`None` here (the explicit-confirm endpoint reports its own).
    """

    text: str
    escalated: bool
    escalation_offered: bool
    support_ticket_id: int | None


def get_or_create_escalation_ticket(
    db: Session,
    chat_session: ChatSession,
    *,
    description: str,
    notification_type: PreferredNotificationMethod = PreferredNotificationMethod.IN_APP,
) -> SupportTicket:
    """Idempotently create (or reuse) this session's escalation ticket,
    mirroring `app.api.chat._get_or_create_feedback_escalation_ticket`'s
    select-first pattern for the same `ChatSession 1 -> 0..1 SupportTicket`
    UNIQUE constraint (`SupportTicket.chat_session_id`, Task 4's schema).

    Called by `POST /chat/messages/{id}/escalate` after the customer says
    yes. A double-click on "Yes", a later "Yes" on another answer in the
    same session, or a session that already has a thumbs-down ticket must
    all reuse the one existing ticket -- so this SELECTs for it FIRST.

    For the theoretical concurrent case -- two simultaneous requests for the
    same session racing each other -- the initial SELECT can still miss a
    not-yet-committed insert from the other request, so the insert itself
    is also wrapped in `try/except IntegrityError` with a re-select
    fallback. Only a genuinely new ticket gets a new `Notification`; a
    reused ticket does not send a duplicate one.

    The insert runs inside a `db.begin_nested()` SAVEPOINT, not a plain
    `try/except` (final-review Fix 6): the caller owns one request-wide
    transaction, so a bare `db.rollback()` here would discard everything
    else that request had staged, not just the failed insert.

    Flushes, never commits -- the caller owns `db.commit()`.
    """
    existing = (
        db.query(SupportTicket)
        .filter(SupportTicket.chat_session_id == chat_session.chat_session_id)
        .one_or_none()
    )
    if existing is not None:
        return existing

    try:
        with db.begin_nested():
            ticket = create_support_ticket(
                db,
                chat_session_id=chat_session.chat_session_id,
                customer_id=chat_session.customer_id,
                device_id=chat_session.device_id,
                subject=_ESCALATION_TICKET_SUBJECT,
                description=description,
            )
    except IntegrityError:
        existing = (
            db.query(SupportTicket)
            .filter(SupportTicket.chat_session_id == chat_session.chat_session_id)
            .one_or_none()
        )
        if existing is None:
            # The IntegrityError wasn't from a concurrent ticket for this
            # session after all -- re-raise rather than swallow an
            # unrelated failure.
            raise
        return existing

    create_notification(
        db,
        support_ticket_id=ticket.support_ticket_id,
        customer_id=chat_session.customer_id,
        notification_type=notification_type,
        message=_ESCALATION_NOTIFICATION_MESSAGE,
    )
    return ticket


def handle_answer(chat_answer: ChatAnswer) -> EscalationResult:
    """Decide whether `chat_answer` is confident enough to show as-is.

    At or above `settings.escalation_confidence_threshold`: returns
    `chat_answer.text` unchanged, `escalation_offered=False`.

    Below it: returns the exact `FALLBACK_TEXT` constant (never the raw LLM
    text) with `escalation_offered=True`. No ticket or notification is
    created -- that only happens if the customer accepts the offer, via
    `POST /chat/messages/{id}/escalate` -> `get_or_create_escalation_ticket`.
    """
    if chat_answer.confidence >= settings.escalation_confidence_threshold:
        return EscalationResult(
            text=chat_answer.text,
            escalated=False,
            escalation_offered=False,
            support_ticket_id=None,
        )
    return EscalationResult(
        text=FALLBACK_TEXT,
        escalated=False,
        escalation_offered=True,
        support_ticket_id=None,
    )
```

In `backend/app/api/chat.py`, make these five edits (the `from app.ai.escalation import handle_answer` import line stays exactly as it is — Task 5 owns the import block):

1. Replace the `ChatResponse` class (from `class ChatResponse(BaseModel):` through `    route_plan: RoutePlanResponse | None = None`) with:

```python
class ChatResponse(BaseModel):
    """`POST /chat` response body.

    `message_id`: the persisted `ChatMessage.chat_message_id` of the
    assistant's turn (added in Task 22) -- the frontend needs this to submit
    thumbs up/down feedback via
    `PATCH /chat/messages/{message_id}/feedback`, and to accept an
    escalation offer via `POST /chat/messages/{message_id}/escalate`, since
    nothing else in this response identifies which `ChatMessage` row the
    answer became.

    `escalation_offered`: True only on a low-confidence RAG turn, where
    `answer` is the fixed fallback text -- the widget should ask the
    customer whether to escalate to a human. Nothing has been escalated yet
    (see docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).

    `escalated`: "a ticket now exists for this exchange". Since escalation
    became opt-in this is always False on `POST /chat` -- it is only ever
    set by the explicit-confirm endpoint (or a thumbs-down), which report
    it on their own responses.
    """

    session_id: int
    message_id: int
    answer: str
    confidence: float
    escalated: bool
    escalation_offered: bool
    route_plan: RoutePlanResponse | None = None
```

2. In the route-plan branch, replace:

```python
                route_plan_payload = route_result
        confidence = 1.0
        escalated = False
        audit_action = ACTION_ROUTE_PLAN_GENERATED
```

with:

```python
                route_plan_payload = route_result
        confidence = 1.0
        escalated = False
        escalation_offered = False
        audit_action = ACTION_ROUTE_PLAN_GENERATED
```

3. In the today's-routes branch, replace:

```python
        answer_text = summarize_todays_routes(db, customer_id=customer_id)
        confidence = 1.0
        escalated = False
        audit_action = ACTION_ROUTE_PLAN_GENERATED
```

with:

```python
        answer_text = summarize_todays_routes(db, customer_id=customer_id)
        confidence = 1.0
        escalated = False
        escalation_offered = False
        audit_action = ACTION_ROUTE_PLAN_GENERATED
```

4. Replace the report branch's tail and the whole RAG `else:` branch:

```python
        answer_text = report_text
        confidence = 1.0
        escalated = False
        audit_action = ACTION_REPORT_GENERATED
    else:
        retrieved_context = retrieve_context(
            payload.query,
            driver_id=payload.driver_id,
            trip_id=payload.trip_id,
            vehicle_id=payload.vehicle_id,
            customer_id=customer_id,
            db=db,
        )
        chat_answer = answer_query(payload.query, retrieved_context)
        escalation_result = handle_answer(db, chat_session.chat_session_id, chat_answer)
        answer_text = escalation_result.text
        confidence = chat_answer.confidence
        escalated = escalation_result.escalated
        audit_action = ACTION_CHAT_ANSWER
```

with:

```python
        answer_text = report_text
        confidence = 1.0
        escalated = False
        escalation_offered = False
        audit_action = ACTION_REPORT_GENERATED
    else:
        retrieved_context = retrieve_context(
            payload.query,
            driver_id=payload.driver_id,
            trip_id=payload.trip_id,
            vehicle_id=payload.vehicle_id,
            customer_id=customer_id,
            db=db,
        )
        chat_answer = answer_query(payload.query, retrieved_context)
        # Opt-in escalation: handle_answer only decides whether to OFFER a
        # human hand-off. It never creates a ticket -- that happens only if
        # the customer accepts, via POST /chat/messages/{id}/escalate.
        escalation_result = handle_answer(chat_answer)
        answer_text = escalation_result.text
        confidence = chat_answer.confidence
        escalated = escalation_result.escalated
        escalation_offered = escalation_result.escalation_offered
        audit_action = ACTION_CHAT_ANSWER
```

5. Replace the audit description and the response construction. Replace:

```python
        description=(
            f"chat_session_id={chat_session.chat_session_id} "
            f"confidence={confidence:.3f} escalated={escalated}"
        ),
```

with:

```python
        description=(
            f"chat_session_id={chat_session.chat_session_id} "
            f"confidence={confidence:.3f} escalated={escalated} "
            f"escalation_offered={escalation_offered}"
        ),
```

and replace:

```python
        confidence=confidence,
        escalated=escalated,
        route_plan=(
```

with:

```python
        confidence=confidence,
        escalated=escalated,
        escalation_offered=escalation_offered,
        route_plan=(
```

In `backend/app/repositories/chat.py`'s module docstring, replace:

```
    themselves (see `app.ai.escalation._get_or_create_escalation_ticket` and
```

with:

```
    themselves (see `app.ai.escalation.get_or_create_escalation_ticket` and
```

In `backend/app/security/audit.py`'s module docstring, replace:

```
  * `ACTION_CHAT_ANSWER` -- Task 15's `POST /chat`, logged once per turn
    after `handle_answer` resolves the final customer-facing text. Notes
    the answer's confidence score and whether it was escalated.
```

with:

```
  * `ACTION_CHAT_ANSWER` -- Task 15's `POST /chat`, logged once per turn
    after `handle_answer` resolves the final customer-facing text. Notes
    the answer's confidence score, `escalated` (always False on this path
    since escalation became opt-in), and `escalation_offered` (whether the
    customer was offered a human hand-off).
```

In the root `.env.example`, replace:

```
# Escalation Configuration
# AI chat answers with a confidence score below this threshold are escalated
# to a human support ticket instead of being shown to the customer.
ESCALATION_CONFIDENCE_THRESHOLD=0.6
```

with:

```
# Escalation Configuration
# AI chat answers with a confidence score below this threshold are replaced
# with a fixed fallback message, and the customer is OFFERED escalation to a
# human. A support ticket (and email, if SMTP is configured below) is only
# created if they say yes -- see POST /chat/messages/{id}/escalate.
ESCALATION_CONFIDENCE_THRESHOLD=0.6
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_escalation.py tests/test_chat_api.py tests/test_audit.py tests/test_feedback_api.py -v`

Expected: all 7 tests in `test_escalation.py` pass; every test in `test_chat_api.py`, `test_audit.py` and `test_feedback_api.py` passes. Then run the whole backend suite to catch any caller missed by the grep: `cd backend && pytest -q` — all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/escalation.py backend/app/api/chat.py backend/app/repositories/chat.py backend/app/security/audit.py .env.example backend/tests/test_escalation.py backend/tests/test_chat_api.py backend/tests/test_audit.py
git commit -m "Make low-confidence escalation opt-in: offer instead of auto-creating a ticket"
```

---

## Task 5: `POST /chat/messages/{chat_message_id}/escalate`

**Dispatch:** Judgment (most capable model). Security-sensitive ownership mirroring, idempotency, and commit-then-email ordering across two failure domains. Reviewer: diff the ownership block against `submit_message_feedback` line by line; confirm nothing reads the DB between `db.commit()` and the email call; confirm the broad `except Exception` wraps only the email call.

**Files:**
- Modify: `backend/app/api/chat.py` (import block lines 35-73; feedback-section comment lines 670-683; `_FEEDBACK_ESCALATION_NOTIFICATION_MESSAGE` comment lines 708-710; `_get_or_create_feedback_escalation_ticket` docstring lines 721-733; new block inserted between `submit_message_feedback` (ends line 810) and `class CesSurveyRequest(BaseModel):`)
- Test: `backend/tests/test_escalate_api.py` (create)

**Interfaces:**
- Consumes:
  - `send_escalation_email(*, customer_name: str, customer_email: str, question: str, answer: str, support_ticket_id: int) -> None` and `EmailDeliveryError` (Task 1).
  - `get_or_create_escalation_ticket(db, chat_session, *, description: str, notification_type=IN_APP) -> SupportTicket` (Task 4).
- Produces:
  - `POST /chat/messages/{chat_message_id}/escalate` (no request body) → `200 {"support_ticket_id": int, "email_sent": bool}`; 401 unauthenticated; 404 missing / not-yours; 400 non-assistant message. **Task 6 calls this.**
  - `ChatMessageEscalateResponse(support_ticket_id: int, email_sent: bool)`.
  - Module-private helpers `_preceding_user_question(db: Session, message: ChatMessage) -> str` and `_escalation_ticket_description(question: str, answer: str) -> str`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_escalate_api.py`:

```python
"""Tests for `POST /chat/messages/{id}/escalate` -- the customer's explicit
"Yes" to an escalation offer (docs/superpowers/specs/2026-09-30-demo-routes-
and-escalation-design.md).

Fixture pattern copied from test_feedback_api.py: the real production `app`
via `TestClient`, `get_db` overridden to an in-memory SQLite session, and
`ChatSession`/`ChatMessage` rows seeded directly via the ORM.

`app.api.chat.send_escalation_email` is patched for EVERY test (autouse
fixture below), so no test can ever reach a real SMTP server -- even on a
developer machine whose own `.env` sets SMTP_HOST. The one test that
exercises the real email module routes the patch back to it with SMTP
deliberately unconfigured, which raises before any connection.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.auth.security import create_access_token, hash_password
from app.config import settings
from app.database import get_db
from app.integrations.email import EmailDeliveryError
from app.integrations.email import send_escalation_email as real_send_escalation_email
from app.main import app
from app.models import (
    Base,
    ChatMessage,
    ChatSession,
    Customer,
    Device,
    Notification,
    SupportTicket,
)
from app.models.enums import (
    AccessLevel,
    BatteryStatus,
    ChatMessageRole,
    DeviceStatus,
    PreferredNotificationMethod,
    SessionStatus,
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(engine, db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        from fastapi.testclient import TestClient

        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def send_email_mock():
    with patch("app.api.chat.send_escalation_email", return_value=None) as mock_send:
        yield mock_send


def _build_case(db_session: Session, *, tag: str) -> dict:
    customer = Customer(
        full_name=f"Escalate Test Customer {tag}",
        email=f"escalate-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)

    device = Device(
        customer_id=customer.customer_id,
        serial_number=f"ESCALATE-{tag}-DEV-001",
        device_type="obd2",
        battery_status=BatteryStatus.OK,
        device_status=DeviceStatus.ACTIVE,
    )
    db_session.add(device)
    db_session.commit()
    db_session.refresh(device)

    chat_session = ChatSession(
        customer_id=customer.customer_id,
        device_id=device.device_id,
        session_status=SessionStatus.ACTIVE,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    user_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        role=ChatMessageRole.USER,
        content=f"{tag}'s question",
    )
    db_session.add(user_message)
    db_session.commit()
    assistant_message = ChatMessage(
        chat_session_id=chat_session.chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content=f"{tag}'s answer",
    )
    db_session.add(assistant_message)
    db_session.commit()
    db_session.refresh(user_message)
    db_session.refresh(assistant_message)

    token = create_access_token(subject=customer.customer_id, role="customer")
    return {
        "customer": customer,
        "device": device,
        "chat_session": chat_session,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture()
def case_a(db_session):
    return _build_case(db_session, tag="A")


@pytest.fixture()
def case_b(db_session):
    return _build_case(db_session, tag="B")


@pytest.fixture()
def support_agent_headers(db_session):
    from app.models.support_agent import SupportAgent

    agent = SupportAgent(
        full_name="Escalate Test Support Agent",
        email="escalate-support-agent@example.test",
        access_level=AccessLevel.TIER_2,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    token = create_access_token(
        subject=agent.support_agent_id, role="support_agent", access_level="tier_2"
    )
    return {"Authorization": f"Bearer {token}"}


def _escalate(client, message_id: int, headers: dict | None):
    return client.post(f"/chat/messages/{message_id}/escalate", headers=headers or {})


def _tickets(db_session, case) -> list[SupportTicket]:
    db_session.expire_all()
    return (
        db_session.query(SupportTicket)
        .filter_by(chat_session_id=case["chat_session"].chat_session_id)
        .all()
    )


# ---------------------------------------------------------------------------
# Auth + ownership (mirrors test_feedback_api.py exactly)
# ---------------------------------------------------------------------------


def test_escalate_requires_auth(client, case_a):
    response = _escalate(client, case_a["assistant_message"].chat_message_id, None)
    assert response.status_code == 401


def test_customer_cannot_escalate_another_customers_message(
    client, db_session, case_a, case_b, send_email_mock
):
    response = _escalate(client, case_b["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 404
    assert _tickets(db_session, case_b) == []
    send_email_mock.assert_not_called()


def test_escalate_on_missing_message_returns_404(client, case_a, send_email_mock):
    response = _escalate(client, 999999, case_a["headers"])

    assert response.status_code == 404
    send_email_mock.assert_not_called()


def test_escalate_on_a_user_message_is_rejected(client, db_session, case_a, send_email_mock):
    response = _escalate(client, case_a["user_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 400
    assert _tickets(db_session, case_a) == []
    send_email_mock.assert_not_called()


def test_support_agent_can_escalate_any_customers_message(
    client, db_session, case_a, support_agent_headers
):
    response = _escalate(
        client, case_a["assistant_message"].chat_message_id, support_agent_headers
    )

    assert response.status_code == 200
    assert len(_tickets(db_session, case_a)) == 1


# ---------------------------------------------------------------------------
# Happy path: ticket + in-app notification, then the email
# ---------------------------------------------------------------------------


def test_escalate_creates_one_ticket_and_one_in_app_notification(client, db_session, case_a):
    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"support_ticket_id", "email_sent"}
    assert body["email_sent"] is True

    tickets = _tickets(db_session, case_a)
    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.support_ticket_id == body["support_ticket_id"]
    assert ticket.customer_id == case_a["customer"].customer_id
    assert ticket.device_id == case_a["device"].device_id
    assert "A's question" in ticket.description
    assert "A's answer" in ticket.description

    notifications = (
        db_session.query(Notification).filter_by(support_ticket_id=ticket.support_ticket_id).all()
    )
    assert len(notifications) == 1
    assert notifications[0].notification_type == PreferredNotificationMethod.IN_APP


def test_email_carries_the_customer_question_answer_and_ticket_id(
    client, case_a, send_email_mock
):
    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    send_email_mock.assert_called_once_with(
        customer_name="Escalate Test Customer A",
        customer_email="escalate-customer-a@example.test",
        question="A's question",
        answer="A's answer",
        support_ticket_id=response.json()["support_ticket_id"],
    )


def test_question_is_the_user_message_immediately_before_the_escalated_answer(
    client, db_session, case_a, send_email_mock
):
    later_question = ChatMessage(
        chat_session_id=case_a["chat_session"].chat_session_id,
        role=ChatMessageRole.USER,
        content="A's later question",
    )
    db_session.add(later_question)
    db_session.commit()
    later_answer = ChatMessage(
        chat_session_id=case_a["chat_session"].chat_session_id,
        role=ChatMessageRole.ASSISTANT,
        content="A's later answer",
    )
    db_session.add(later_answer)
    db_session.commit()
    db_session.refresh(later_answer)

    _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])
    _escalate(client, later_answer.chat_message_id, case_a["headers"])

    first_call, second_call = send_email_mock.call_args_list
    assert first_call.kwargs["question"] == "A's question"
    assert second_call.kwargs["question"] == "A's later question"
    assert second_call.kwargs["answer"] == "A's later answer"


def test_email_is_sent_only_after_the_ticket_is_committed(
    client, db_session, case_a, send_email_mock
):
    """Ordering guarantee behind the two failure domains: by the time the
    email is attempted, the ticket must already be durable -- the session
    has no open transaction left to roll back."""
    observed: dict[str, bool] = {}

    def _record_transaction_state(**kwargs):
        observed["in_transaction"] = db_session.in_transaction()

    send_email_mock.side_effect = _record_transaction_state

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    assert observed == {"in_transaction": False}


# ---------------------------------------------------------------------------
# Two failure domains: a failed email never loses the ticket, never 5xx
# ---------------------------------------------------------------------------


def test_ticket_is_created_even_when_the_email_raises(client, db_session, case_a, send_email_mock):
    send_email_mock.side_effect = EmailDeliveryError(
        "Escalation email could not be sent: Connection refused"
    )

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["email_sent"] is False
    tickets = _tickets(db_session, case_a)
    assert len(tickets) == 1
    assert tickets[0].support_ticket_id == body["support_ticket_id"]


def test_an_unexpected_email_error_still_degrades_to_email_not_sent(
    client, db_session, case_a, send_email_mock
):
    send_email_mock.side_effect = RuntimeError("something nobody anticipated")

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    assert response.json()["email_sent"] is False
    assert len(_tickets(db_session, case_a)) == 1


def test_unconfigured_smtp_degrades_to_ticket_created_email_not_sent(
    client, db_session, case_a, send_email_mock, monkeypatch
):
    """End-to-end through the REAL email module with SMTP_HOST empty: it
    raises EmailNotConfiguredError before connecting, and the endpoint
    still returns the committed ticket."""
    monkeypatch.setattr(settings, "smtp_host", "")
    send_email_mock.side_effect = real_send_escalation_email

    with patch("app.integrations.email.smtplib.SMTP") as mock_smtp_cls:
        response = _escalate(
            client, case_a["assistant_message"].chat_message_id, case_a["headers"]
        )

    mock_smtp_cls.assert_not_called()
    assert response.status_code == 200
    assert response.json()["email_sent"] is False
    assert len(_tickets(db_session, case_a)) == 1


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_double_click_creates_one_ticket_not_two(client, db_session, case_a, send_email_mock):
    first = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])
    second = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["support_ticket_id"] == first.json()["support_ticket_id"]

    tickets = _tickets(db_session, case_a)
    assert len(tickets) == 1
    assert (
        db_session.query(Notification)
        .filter_by(support_ticket_id=tickets[0].support_ticket_id)
        .count()
        == 1
    )
    # Documented policy: every successful "Yes" attempts the email, even on
    # a reused ticket -- never report email_sent=false for a mail that was
    # never tried. The widget disables "Yes" while a request is in flight.
    assert send_email_mock.call_count == 2


def test_escalate_reuses_a_ticket_created_earlier_by_a_thumbs_down(client, db_session, case_a):
    feedback = client.patch(
        f"/chat/messages/{case_a['assistant_message'].chat_message_id}/feedback",
        json={"feedback": False},
        headers=case_a["headers"],
    )
    assert feedback.status_code == 200

    response = _escalate(client, case_a["assistant_message"].chat_message_id, case_a["headers"])

    assert response.status_code == 200
    assert response.json()["support_ticket_id"] == feedback.json()["support_ticket_id"]
    assert len(_tickets(db_session, case_a)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_escalate_api.py -v`

Expected: every one of the 14 tests errors during setup of the autouse `send_email_mock` fixture with `AttributeError: <module 'app.api.chat' ...> does not have the attribute 'send_escalation_email'` — the endpoint and its import don't exist yet.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/api/chat.py`, edit the import block:

Replace:

```python
import re
from dataclasses import dataclass
```

with:

```python
import logging
import re
from dataclasses import dataclass
```

Replace:

```python
from app.ai.escalation import handle_answer
```

with:

```python
from app.ai.escalation import get_or_create_escalation_ticket, handle_answer
```

Replace:

```python
from app.database import get_db
```

with:

```python
from app.database import get_db
from app.integrations.email import send_escalation_email
```

Replace:

```python
from app.models.device import Device
```

with:

```python
from app.models.customer import Customer
from app.models.device import Device
```

Replace:

```python
router = APIRouter(tags=["chat"])
```

with:

```python
router = APIRouter(tags=["chat"])

logger = logging.getLogger(__name__)
```

Refresh three comments made stale by Task 4. Replace:

```python
# `create_support_ticket` docstring). Task 14's `handle_answer` never has to
# worry about a duplicate because a session is only auto-escalated once (at
# most one low-confidence answer triggers it, and nothing re-runs
# `handle_answer` for an already-escalated session). Feedback is different:
# a customer can press thumbs-down on the same message repeatedly (double
# click, retry after a flaky network response, etc.), and a session's
# assistant answer might ALREADY have been auto-escalated by Task 14 before
# the customer ever presses thumbs-down. Both cases must be a no-op, not an
```

with:

```python
# `create_support_ticket` docstring). A customer can press thumbs-down on
# the same message repeatedly (double click, retry after a flaky network
# response, etc.), and a session might ALREADY have a ticket because the
# customer accepted an escalation offer (`POST /chat/messages/{id}/escalate`,
# below) before ever pressing thumbs-down. Both cases must be a no-op, not an
```

Replace:

```python
#: Message stored on the `Notification` created when a thumbs-down triggers
#: an escalation ticket -- mirrors `app.ai.escalation._ESCALATION_NOTIFICATION_MESSAGE`'s
#: wording/purpose for the low-confidence auto-escalation path.
```

with:

```python
#: Message stored on the `Notification` created when a thumbs-down triggers
#: an escalation ticket -- mirrors `app.ai.escalation._ESCALATION_NOTIFICATION_MESSAGE`'s
#: wording/purpose for the opt-in escalation path.
```

In `_get_or_create_feedback_escalation_ticket`'s docstring, replace:

```python
    `create_notification` (the same repository functions Task 14's
    `handle_answer` uses for the low-confidence auto-escalation path).
```

with:

```python
    `create_notification` (the same repository functions
    `app.ai.escalation.get_or_create_escalation_ticket` uses for the opt-in
    escalation path).
```

and replace:

```python
    on a duplicate insert -- so pressing thumbs-down twice (or thumbs-down
    on a session Task 14 already auto-escalated for low confidence) reuses
    the existing ticket instead of erroring.
```

with:

```python
    on a duplicate insert -- so pressing thumbs-down twice (or thumbs-down
    on a session the customer already escalated via
    `POST /chat/messages/{id}/escalate`) reuses the existing ticket instead
    of erroring.
```

Insert the new endpoint block between the end of `submit_message_feedback` (its final `    )` after `support_ticket_id=support_ticket_id,`) and the line `class CesSurveyRequest(BaseModel):` — keep two blank lines on each side:

```python
# ---------------------------------------------------------------------------
# `POST /chat/messages/{id}/escalate` -- the customer's explicit "Yes" to the
# escalation offer a low-confidence answer carries
# (`ChatResponse.escalation_offered`). See
# docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md.
#
# Ownership is `submit_message_feedback`'s exactly: 404 for a missing
# message, the SAME 404 when a customer-role caller doesn't own the
# resolved session, 400 for a non-assistant message, support_agent
# unrestricted.
#
# Two failure domains by design: the ticket + in-app notification are
# committed FIRST, and only then is the external email attempted, inside
# its own try/except. A down or unconfigured SMTP server degrades to
# "ticket created, email not sent" (`email_sent=false`) -- never to a
# rolled-back ticket or a 5xx.
# ---------------------------------------------------------------------------


class ChatMessageEscalateResponse(BaseModel):
    """`POST /chat/messages/{id}/escalate` response body.

    `support_ticket_id` always identifies a committed ticket (new, or this
    session's existing one). `email_sent` is False when the notification
    email could not be sent -- the ticket exists either way.
    """

    support_ticket_id: int
    email_sent: bool


def _preceding_user_question(db: Session, message: ChatMessage) -> str:
    """The customer's question this assistant `message` answered: the
    `role=user` message immediately before it in the same session. `""` if
    there is none (never expected for a message written by `POST /chat`,
    which always persists the user/assistant pair together)."""
    question = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.chat_session_id == message.chat_session_id,
            ChatMessage.role == ChatMessageRole.USER,
            ChatMessage.chat_message_id < message.chat_message_id,
        )
        .order_by(ChatMessage.chat_message_id.desc())
        .first()
    )
    return question.content if question is not None else ""


def _escalation_ticket_description(question: str, answer: str) -> str:
    return (
        f"Customer question: {question or '(no preceding question found)'}\n\n"
        f"Assistant answer: {answer}"
    )


@router.post(
    "/chat/messages/{chat_message_id}/escalate", response_model=ChatMessageEscalateResponse
)
def escalate_message(
    chat_message_id: int,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(_allowed_roles),
) -> ChatMessageEscalateResponse:
    message = db.get(ChatMessage, chat_message_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat message not found")

    chat_session = db.get(ChatSession, message.chat_session_id)
    if chat_session is None or (
        current_user.role == "customer" and chat_session.customer_id != current_user.user_id
    ):
        # Identical 404 for "no such message" and "exists but isn't yours" --
        # same cross-tenant posture as `submit_message_feedback` above.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Chat message not found")

    if message.role != ChatMessageRole.ASSISTANT:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Only an assistant message can be escalated",
        )

    question = _preceding_user_question(db, message)
    answer = message.content

    # Idempotent: a double-click, a second "Yes" in the same session, or a
    # session that already has a thumbs-down ticket all reuse the one
    # existing ticket (select-first + IntegrityError SAVEPOINT fallback).
    ticket = get_or_create_escalation_ticket(
        db, chat_session, description=_escalation_ticket_description(question, answer)
    )
    customer = db.get(Customer, chat_session.customer_id)

    # Captured BEFORE the commit below: committing expires every loaded
    # object, and nothing after the commit may touch the database -- the
    # email is a separate failure domain from the ticket.
    support_ticket_id = ticket.support_ticket_id
    customer_name = customer.full_name
    customer_email = customer.email

    # Failure domain 1: the ticket + in-app notification become durable here.
    db.commit()

    # Failure domain 2: best-effort external email. Deliberately broad --
    # the ticket is already committed, so NO failure in here (SMTP down,
    # misconfigured, or anything unforeseen) may turn into a 5xx or make
    # the customer think escalation failed when it didn't.
    email_sent = False
    try:
        send_escalation_email(
            customer_name=customer_name,
            customer_email=customer_email,
            question=question,
            answer=answer,
            support_ticket_id=support_ticket_id,
        )
        email_sent = True
    except Exception as exc:  # noqa: BLE001 -- see comment above
        logger.warning(
            "Escalation email for support ticket #%s was not sent: %s", support_ticket_id, exc
        )

    return ChatMessageEscalateResponse(support_ticket_id=support_ticket_id, email_sent=email_sent)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_escalate_api.py tests/test_feedback_api.py tests/test_chat_api.py -v`

Expected: all 14 tests in `test_escalate_api.py` pass; `test_feedback_api.py` and `test_chat_api.py` still pass in full. Then `cd backend && pytest -q` — whole suite passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/chat.py backend/tests/test_escalate_api.py
git commit -m "Add POST /chat/messages/{id}/escalate: commit the ticket, then best-effort email"
```

---

## Task 6: Yes/No escalation prompt in `ChatWidget`

**Dispatch:** Judgment (standard model). New per-message UI state (offered → sending → confirmed, or dismissed; failure rolls back to offered). Reviewer: confirm **No** sends nothing, **Yes** is disabled while in flight, and the confirmation copy matches Global Constraints exactly.

**Files:**
- Modify: `frontend/src/types/chat.ts` (`ChatResponse` lines 29-42; append new interface after `ChatMessageFeedbackResponse`, line 59)
- Modify: `frontend/src/components/ChatWidget.tsx` (module docstring lines 45-63; type import line 70; `ChatWidgetMessage` lines 74-91; new callbacks after `handleFeedback`, line 282; assistant message construction lines 331-338; render between the bubble (ends line 435) and the feedback controls (line 436))
- Test: `frontend/src/components/ChatWidget.test.tsx` (`mockChatFetch`, lines 117-174; append a `describe` block before the final `})` at line 773)

**Interfaces:**
- Consumes: `ChatResponse.escalation_offered: bool` (Task 4); `POST /chat/messages/{message_id}/escalate` → `{support_ticket_id: number, email_sent: boolean}` (Task 5).
- Produces: `ChatResponse.escalation_offered: boolean` and `ChatMessageEscalateResponse { support_ticket_id: number; email_sent: boolean }` in `frontend/src/types/chat.ts`; DOM test ids `chat-escalation-offer` and `chat-escalation-confirmation`. Nothing later consumes them.

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/ChatWidget.test.tsx`, replace the whole `mockChatFetch` function and its doc comment (from `/**\n * Routes mocked \`fetch\` calls to` through the function's closing `}`) with:

```tsx
/**
 * Routes mocked `fetch` calls to `GET /devices` (customer-100-scoped by
 * default, customer-200-scoped when `?customer_id=200` is present),
 * `POST /chat`, `PATCH /chat/messages/{id}/feedback`,
 * `POST /chat/messages/{id}/escalate`, and `POST /chat/sessions/{id}/survey`
 * fixture responses.
 *
 * `/feedback`, `/escalate` and `/survey` are checked BEFORE the generic
 * `/chat` check below, since all three URLs also contain the substring
 * `/chat`.
 */
function mockChatFetch({
  escalated = false,
  escalationOffered = false,
  emailSent = true,
}: { escalated?: boolean; escalationOffered?: boolean; emailSent?: boolean } = {}) {
  ;(fetch as unknown as Mock).mockImplementation(async (url: string, options?: RequestInit) => {
    if (url.includes('/feedback')) {
      const body = JSON.parse((options?.body as string) ?? '{}')
      return {
        ok: true,
        status: 200,
        json: async () => ({
          chat_message_id: 555,
          feedback: body.feedback,
          escalated: body.feedback === false,
          support_ticket_id: body.feedback === false ? 901 : null,
        }),
      }
    }
    if (url.includes('/escalate')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ support_ticket_id: 901, email_sent: emailSent }),
      }
    }
    if (url.includes('/survey')) {
      const body = JSON.parse((options?.body as string) ?? '{}')
      return {
        ok: true,
        status: 200,
        json: async () => ({ chat_session_id: 42, ces_score: body.score }),
      }
    }
    if (url.includes('/devices')) {
      const match = url.match(/customer_id=(\d+)/)
      if (match && Number(match[1]) === 200) {
        return { ok: true, status: 200, json: async () => customerBDevices }
      }
      return { ok: true, status: 200, json: async () => customerADevices }
    }
    if (url.includes('/chat')) {
      const notConfident = escalated || escalationOffered
      return {
        ok: true,
        status: 200,
        json: async () => ({
          session_id: 42,
          message_id: 555,
          answer: notConfident
            ? "I'm not confident enough to answer that -- a human agent will follow up."
            : 'Your vehicle traveled 42.5 km on its last trip.',
          confidence: notConfident ? 0.1 : 0.95,
          escalated,
          escalation_offered: escalationOffered,
        }),
      }
    }
    throw new Error(`Unexpected fetch to ${url}`)
  })
}
```

Then insert this `describe` block immediately before the file's final `})` (the one closing `describe('ChatWidget', ...)`, after the `describe('route-planning + warnings', ...)` block):

```tsx
  describe('opt-in escalation offer', () => {
    const NOT_CONFIDENT = "I'm not confident enough to answer that -- a human agent will follow up."

    async function sendAndWaitForNotConfidentAnswer() {
      await openWidget()
      await sendMessage('Why is my device offline?')
      await waitFor(() => {
        expect(screen.getByText(NOT_CONFIDENT)).toBeInTheDocument()
      })
    }

    function escalateCalls() {
      return (fetch as unknown as Mock).mock.calls.filter(([url]) => (url as string).includes('/escalate'))
    }

    it('shows the Yes/No prompt on an answer whose escalation_offered is true', async () => {
      mockChatFetch({ escalationOffered: true })
      renderWidget()

      await sendAndWaitForNotConfidentAnswer()

      const offer = screen.getByTestId('chat-escalation-offer')
      expect(offer).toHaveTextContent('Would you like to escalate this to a human?')
      expect(screen.getByRole('button', { name: /^yes$/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^no$/i })).toBeInTheDocument()
      // Offered is not escalated: no amber "Escalated" label yet.
      expect(screen.queryByTestId('chat-escalation-label')).not.toBeInTheDocument()
    })

    it('does not show the prompt when escalation_offered is false', async () => {
      mockChatFetch()
      renderWidget()

      await openWidget()
      await sendMessage('How far did my vehicle travel?')
      await waitFor(() => {
        expect(screen.getByText('Your vehicle traveled 42.5 km on its last trip.')).toBeInTheDocument()
      })

      expect(screen.queryByTestId('chat-escalation-offer')).not.toBeInTheDocument()
    })

    it('Yes POSTs /chat/messages/{message_id}/escalate and replaces the prompt with the ref-number confirmation', async () => {
      mockChatFetch({ escalationOffered: true })
      renderWidget()
      await sendAndWaitForNotConfidentAnswer()

      fireEvent.click(screen.getByRole('button', { name: /^yes$/i }))

      await waitFor(() => {
        expect(screen.getByTestId('chat-escalation-confirmation')).toBeInTheDocument()
      })
      const confirmation = screen.getByTestId('chat-escalation-confirmation')
      expect(confirmation).toHaveTextContent('A support agent has been notified (ref #901)')
      expect(confirmation).not.toHaveTextContent(/could not be sent/)
      expect(screen.queryByTestId('chat-escalation-offer')).not.toBeInTheDocument()
      // The message now reads as escalated, same as the thumbs-down path.
      expect(screen.getByTestId('chat-escalation-label')).toBeInTheDocument()

      const calls = escalateCalls()
      expect(calls).toHaveLength(1)
      const [escalateUrl, options] = calls[0] as [string, RequestInit]
      expect(escalateUrl).toContain('/chat/messages/555/escalate')
      expect(options.method).toBe('POST')
    })

    it('says the email could not be sent when email_sent is false, without saying escalation failed', async () => {
      mockChatFetch({ escalationOffered: true, emailSent: false })
      renderWidget()
      await sendAndWaitForNotConfidentAnswer()

      fireEvent.click(screen.getByRole('button', { name: /^yes$/i }))

      await waitFor(() => {
        expect(screen.getByTestId('chat-escalation-confirmation')).toHaveTextContent(
          'A support agent has been notified (ref #901) (email notification could not be sent)'
        )
      })
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('No dismisses the prompt locally and sends no request', async () => {
      mockChatFetch({ escalationOffered: true })
      renderWidget()
      await sendAndWaitForNotConfidentAnswer()

      fireEvent.click(screen.getByRole('button', { name: /^no$/i }))

      expect(screen.queryByTestId('chat-escalation-offer')).not.toBeInTheDocument()
      expect(screen.queryByTestId('chat-escalation-confirmation')).not.toBeInTheDocument()
      expect(escalateCalls()).toHaveLength(0)
      // Thumbs up/down stay available on the same message.
      expect(screen.getByTestId('chat-feedback-controls')).toBeInTheDocument()
    })

    it('disables Yes while the request is in flight, so a double-click sends exactly one request', async () => {
      mockChatFetch({ escalationOffered: true })
      renderWidget()
      await sendAndWaitForNotConfidentAnswer()

      let resolveEscalate: (value: unknown) => void = () => {}
      ;(fetch as unknown as Mock).mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveEscalate = resolve
          })
      )

      const yesButton = screen.getByRole('button', { name: /^yes$/i })
      fireEvent.click(yesButton)
      expect(yesButton).toBeDisabled()
      fireEvent.click(yesButton)

      resolveEscalate({
        ok: true,
        status: 200,
        json: async () => ({ support_ticket_id: 901, email_sent: true }),
      })
      await waitFor(() => {
        expect(screen.getByTestId('chat-escalation-confirmation')).toBeInTheDocument()
      })
      expect(escalateCalls()).toHaveLength(1)
    })

    it('restores the prompt and shows an error when the escalate request fails', async () => {
      mockChatFetch({ escalationOffered: true })
      renderWidget()
      await sendAndWaitForNotConfidentAnswer()

      ;(fetch as unknown as Mock).mockImplementationOnce(async () => ({
        ok: false,
        status: 500,
        statusText: 'Internal Server Error',
        json: async () => ({}),
      }))

      fireEvent.click(screen.getByRole('button', { name: /^yes$/i }))

      await waitFor(() => {
        expect(screen.getByRole('alert')).toBeInTheDocument()
      })
      expect(screen.getByTestId('chat-escalation-offer')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^yes$/i })).not.toBeDisabled()
      expect(screen.queryByTestId('chat-escalation-confirmation')).not.toBeInTheDocument()
    })
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/ChatWidget.test.tsx`

Expected: the 7 new tests in `opt-in escalation offer` — 6 fail (`Unable to find an element by: [data-testid="chat-escalation-offer"]` or `...confirmation`, or `Unable to find role="button" and name /^yes$/i`), and `does not show the prompt when escalation_offered is false` passes (it is a guard). All pre-existing tests still pass with the reworked `mockChatFetch` (its default output only gained `escalation_offered: false`).

- [ ] **Step 3: Write minimal implementation**

In `frontend/src/types/chat.ts`, replace the `ChatResponse` interface with:

```ts
export interface ChatResponse {
  session_id: number
  /** `ChatMessage.chat_message_id` of the assistant's turn (Task 22) --
   * needed to submit thumbs up/down feedback via
   * `PATCH /chat/messages/{message_id}/feedback`, and to accept an
   * escalation offer via `POST /chat/messages/{message_id}/escalate`. */
  message_id: number
  answer: string
  confidence: number
  /** "A ticket now exists for this exchange." Always `false` from
   * `POST /chat` since escalation became opt-in. */
  escalated: boolean
  /** `true` on a low-confidence answer: the widget should ASK whether to
   * escalate to a human. Nothing has been escalated yet. */
  escalation_offered: boolean
  /** Structured route data -- only present when this turn answered a
   * route-plan chat intent successfully (route-planning + warnings
   * feature). See backend/app/api/chat.py's ChatResponse. */
  route_plan?: RoutePlanResult | null
}
```

and insert immediately after the `ChatMessageFeedbackResponse` interface:

```ts
/** `POST /chat/messages/{message_id}/escalate` response
 * (`ChatMessageEscalateResponse` in `backend/app/api/chat.py`). The ticket
 * always exists on a 200; `email_sent: false` only means the notification
 * email could not be delivered. */
export interface ChatMessageEscalateResponse {
  support_ticket_id: number
  email_sent: boolean
}
```

In `frontend/src/components/ChatWidget.tsx`:

Replace the end of the module docstring:

```tsx
 * whether the user submits a score or skips.
 */
```

with:

```tsx
 * whether the user submits a score or skips.
 *
 * **Opt-in escalation** (docs/superpowers/specs/2026-09-30-demo-routes-and-
 * escalation-design.md): a low-confidence answer arrives with
 * `escalation_offered: true` instead of an auto-created ticket. That message
 * shows "Would you like to escalate this to a human? [Yes] [No]" in the same
 * spot as the feedback buttons. **No** is purely local (nothing is sent).
 * **Yes** calls `POST /chat/messages/{message_id}/escalate` (disabled while
 * in flight, so a double-click sends one request) and then shows "A support
 * agent has been notified (ref #N)" -- with "(email notification could not
 * be sent)" appended when `email_sent` is false, since the ticket exists
 * either way -- and marks the message `escalated`, reusing the same amber
 * label a thumbs-down escalation already gets. A failed request restores
 * the prompt and surfaces the error.
 */
```

Replace the type import line:

```tsx
import type { ChatMessageFeedbackResponse, ChatRequest, ChatResponse } from '../types/chat'
```

with:

```tsx
import type {
  ChatMessageEscalateResponse,
  ChatMessageFeedbackResponse,
  ChatRequest,
  ChatResponse,
} from '../types/chat'
```

Replace the `ChatWidgetMessage` interface with:

```tsx
/** Lifecycle of an escalation offer on one assistant message:
 * `offered` (prompt shown) -> `sending` (Yes clicked, request in flight) ->
 * `confirmed`; or `offered` -> `dismissed` (No). A failed request goes
 * `sending` -> back to `offered`. */
type EscalationStatus = 'offered' | 'sending' | 'dismissed' | 'confirmed'

interface ChatWidgetMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** Only ever `true` on an assistant message -- see `ChatResponse.escalated`. */
  escalated?: boolean
  /** `ChatMessage.chat_message_id` (Task 22) -- only set on assistant
   * messages, since only those can receive feedback. Used to target
   * `PATCH /chat/messages/{chatMessageId}/feedback` and
   * `POST /chat/messages/{chatMessageId}/escalate`. */
  chatMessageId?: number
  /** Thumbs up (`true`) / down (`false`) / not yet rated (`undefined`).
   * Only meaningful on assistant messages. */
  feedback?: boolean
  /** Structured route data (route-planning + warnings feature) -- only set
   * on an assistant message that answered a route-plan chat intent
   * successfully. Rendered as an inline map beside the transcript. */
  routePlan?: RoutePlanResult
  /** Set only on an assistant message whose response had
   * `escalation_offered: true`. See `EscalationStatus`. */
  escalationStatus?: EscalationStatus
  /** From `ChatMessageEscalateResponse`, once `escalationStatus` is `confirmed`. */
  escalationTicketId?: number
  escalationEmailSent?: boolean
}
```

Insert immediately after the end of `handleFeedback` (its closing `}, [])`):

```tsx
  const patchMessage = useCallback((messageId: string, patch: Partial<ChatWidgetMessage>) => {
    setMessages((prev) => prev.map((message) => (message.id === messageId ? { ...message, ...patch } : message)))
  }, [])

  const handleEscalationChoice = useCallback(
    async (messageId: string, chatMessageId: number, accept: boolean) => {
      if (!accept) {
        // "No" is purely local -- nothing is sent to the backend.
        patchMessage(messageId, { escalationStatus: 'dismissed' })
        return
      }

      patchMessage(messageId, { escalationStatus: 'sending' })
      try {
        const response = await apiPost<ChatMessageEscalateResponse>(`/chat/messages/${chatMessageId}/escalate`, {})
        patchMessage(messageId, {
          escalationStatus: 'confirmed',
          escalationTicketId: response.support_ticket_id,
          escalationEmailSent: response.email_sent,
          // A ticket now exists -- reuse the same "Escalated" rendering the
          // thumbs-down path already gives (see handleFeedback above).
          escalated: true,
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to escalate to a human.')
        // Nothing was escalated -- put the offer back so the user can retry.
        patchMessage(messageId, { escalationStatus: 'offered' })
      }
    },
    [patchMessage]
  )
```

In `handleSend`, replace the assistant message construction:

```tsx
          content: response.answer,
          escalated: response.escalated,
          chatMessageId: response.message_id,
          routePlan: response.route_plan ?? undefined,
```

with:

```tsx
          content: response.answer,
          escalated: response.escalated,
          chatMessageId: response.message_id,
          routePlan: response.route_plan ?? undefined,
          escalationStatus: response.escalation_offered ? 'offered' : undefined,
```

In the render, insert the prompt/confirmation between the message bubble and the feedback controls — i.e. replace:

```tsx
                      <p>{message.content}</p>
                    </div>
                    {message.role === 'assistant' && message.chatMessageId !== undefined && (
                      <div
                        data-testid="chat-feedback-controls"
```

with:

```tsx
                      <p>{message.content}</p>
                    </div>
                    {message.role === 'assistant' &&
                      message.chatMessageId !== undefined &&
                      (message.escalationStatus === 'offered' || message.escalationStatus === 'sending') && (
                        <div
                          data-testid="chat-escalation-offer"
                          className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300"
                        >
                          <span>Would you like to escalate this to a human?</span>
                          <button
                            type="button"
                            disabled={message.escalationStatus === 'sending'}
                            onClick={() =>
                              void handleEscalationChoice(message.id, message.chatMessageId as number, true)
                            }
                            className="rounded bg-indigo-600 px-2 py-0.5 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                          >
                            Yes
                          </button>
                          <button
                            type="button"
                            disabled={message.escalationStatus === 'sending'}
                            onClick={() =>
                              void handleEscalationChoice(message.id, message.chatMessageId as number, false)
                            }
                            className="rounded border border-gray-300 px-2 py-0.5 font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-700"
                          >
                            No
                          </button>
                        </div>
                      )}
                    {message.role === 'assistant' && message.escalationStatus === 'confirmed' && (
                      <p
                        data-testid="chat-escalation-confirmation"
                        className="mt-1 text-xs text-gray-600 dark:text-gray-300"
                      >
                        A support agent has been notified (ref #{message.escalationTicketId})
                        {message.escalationEmailSent ? '' : ' (email notification could not be sent)'}
                      </p>
                    )}
                    {message.role === 'assistant' && message.chatMessageId !== undefined && (
                      <div
                        data-testid="chat-feedback-controls"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/components/ChatWidget.test.tsx && npx tsc -b`

Expected: every test in `ChatWidget.test.tsx` passes (pre-existing + 7 new), and `tsc -b` exits 0 with no type errors. Then `cd frontend && npx vitest run` — the whole frontend suite passes.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/chat.ts frontend/src/components/ChatWidget.tsx frontend/src/components/ChatWidget.test.tsx
git commit -m "Add the Yes/No escalation offer to the chat widget"
```

---

## Task 7: `app/seed/seed_demo_routes.py`

**Dispatch:** Mechanical-ish (standard model) — every line is given, but the reviewer should check the time-window arithmetic (every row lands inside today's Sydney window and never in the future) and that drivers are only ever drawn from the row's own customer's fleet.

**Files:**
- Create: `backend/app/seed/seed_demo_routes.py`
- Test: `backend/tests/test_seed_demo_routes.py`

**Interfaces:**
- Consumes: `RoutePlan`, `Driver`, `Customer`, `SupportAgent`, `RoutePlanStatus`; `SITE_TZ`, `site_day_bounds` (`app.timeutil`); `summarize_todays_routes` (Task 3, test only); `_row_to_list_item` (`app/api/route_plan.py`, test only); `compute_route_progress` (`app/ai/route_tracking.py`, test only).
- Produces:
  - `seed_demo_routes(db: Session, *, rng: random.Random, customer_ids: list[int] | None = None, customer_count: int = DEFAULT_CUSTOMER_COUNT, now: datetime | None = None) -> DemoRouteSummary` — flushes, never commits.
  - `DemoRouteSummary(created: dict[int, dict[str, int]], skipped: dict[int, str])` with property `total_created: int`.
  - `DemoSeedError(RuntimeError)`, `DemoPlace`, `DemoRoutePair`, `DEMO_ROUTE_PAIRS`, `DEMO_WARNING_POOL`, `DEFAULT_CUSTOMER_COUNT = 3`.
  - `run(*, customer_ids=None, customer_count=DEFAULT_CUSTOMER_COUNT, seed=None) -> DemoRouteSummary` and `main(argv: list[str] | None = None) -> None` (CLI flags `--customer-id N` repeatable, `--customers N`, `--seed N`). Task 8 documents these.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_seed_demo_routes.py`:

```python
"""Smoke tests for app.seed.seed_demo_routes -- the manual demo-route seed
script (docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).

Same in-memory SQLite (FK enforcement on) pattern as test_seed.py. The key
property is that every row the script writes round-trips through the REAL
model and the REAL API/response validators every consumer uses --
`_row_to_list_item` (-> `WarningOut`) and `compute_route_progress` -- so the
chat summary, reports, Routes page and live map all accept seeded rows with
no format branching.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import summarize_todays_routes
from app.ai.route_tracking import compute_route_progress
from app.api.route_plan import _row_to_list_item
from app.auth.security import hash_password
from app.models import Base, Customer, Driver, RoutePlan, SupportAgent
from app.models.enums import AccessLevel, PreferredNotificationMethod, RoutePlanStatus
from app.seed import seed_demo_routes as seed_module
from app.seed.seed_demo_routes import (
    DEMO_ROUTE_PAIRS,
    DEMO_WARNING_POOL,
    DemoSeedError,
    seed_demo_routes,
)
from app.timeutil import SITE_TZ, site_day_bounds

_WARNING_KEYS = {"location", "distance_from_origin_km", "type", "severity", "description"}


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Demo Customer {tag}",
        email=f"demo-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_driver(db_session: Session, *, customer: Customer, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Demo Driver {tag}",
        license_number=f"LIC-DEMO-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def _make_agent(db_session: Session) -> SupportAgent:
    agent = SupportAgent(
        full_name="Demo Dispatch Agent",
        email="demo-dispatch-agent@example.test",
        access_level=AccessLevel.TIER_1,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture()
def world(db_session):
    """Customers created in id order A < B < C: A has two drivers, B has
    one, C has none (must be skipped). One support agent to attribute the
    simulated dispatch to."""
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    customer_c = _make_customer(db_session, tag="C")
    drivers_a = [
        _make_driver(db_session, customer=customer_a, tag="A1"),
        _make_driver(db_session, customer=customer_a, tag="A2"),
    ]
    drivers_b = [_make_driver(db_session, customer=customer_b, tag="B1")]
    agent = _make_agent(db_session)
    return {
        "customer_a": customer_a,
        "customer_b": customer_b,
        "customer_c": customer_c,
        "fleets": {
            customer_a.customer_id: {d.driver_id for d in drivers_a},
            customer_b.customer_id: {d.driver_id for d in drivers_b},
        },
        "agent": agent,
    }


def _as_utc(value: datetime) -> datetime:
    """SQLite returns tz-naive values for DateTime(timezone=True) columns."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _seed_and_reload(db_session, *, now: datetime, **kwargs) -> tuple[object, list[RoutePlan]]:
    summary = seed_demo_routes(db_session, rng=random.Random(7), now=now, **kwargs)
    db_session.commit()
    db_session.expire_all()
    return summary, db_session.query(RoutePlan).all()


def test_every_created_row_round_trips_through_the_real_model_and_api_validators(
    db_session, world
):
    now = datetime.now(timezone.utc)
    summary, rows = _seed_and_reload(db_session, now=now)

    assert summary.total_created == len(rows) > 0
    for row in rows:
        item = _row_to_list_item(row)  # validates every warning via WarningOut
        assert item.unavailable is False
        progress = compute_route_progress(row, now=now)
        assert 0.0 <= progress.progress_percent <= 100.0


def test_rows_are_dated_today_in_sydney_with_an_active_and_completed_mix(db_session, world):
    now = datetime.now(timezone.utc)
    _, rows = _seed_and_reload(db_session, now=now)
    start, end = site_day_bounds(now.astimezone(SITE_TZ).date())

    for row in rows:
        created_at = _as_utc(row.created_at)
        assert start <= created_at <= now < end
        if row.status == RoutePlanStatus.COMPLETED:
            assert row.completed_at is not None
            assert created_at <= _as_utc(row.completed_at) <= now
        else:
            assert row.completed_at is None

    for key in ("customer_a", "customer_b"):
        statuses = {r.status for r in rows if r.customer_id == world[key].customer_id}
        assert statuses == {RoutePlanStatus.ACTIVE, RoutePlanStatus.COMPLETED}


def test_drivers_come_only_from_the_rows_own_customer_and_dispatch_is_a_support_agent(
    db_session, world
):
    _, rows = _seed_and_reload(db_session, now=datetime.now(timezone.utc))

    for row in rows:
        if row.driver_id is not None:
            assert row.driver_id in world["fleets"][row.customer_id]
        assert row.created_by_role == "support_agent"
        assert row.created_by_id == world["agent"].support_agent_id


def test_labels_geometry_and_warnings_use_the_demo_shapes(db_session, world):
    _, rows = _seed_and_reload(db_session, now=datetime.now(timezone.utc))
    known_pairs = {(p.origin.label, p.destination.label) for p in DEMO_ROUTE_PAIRS}

    for row in rows:
        assert (row.origin_label, row.destination_label) in known_pairs
        assert row.geometry["type"] == "LineString"
        assert len(row.geometry["coordinates"]) == 2
        assert 0 <= len(row.warnings) <= 3
        for warning in row.warnings:
            assert set(warning) == _WARNING_KEYS
            assert (warning["type"], warning["severity"], warning["description"]) in DEMO_WARNING_POOL
            assert warning["type"] in {"weather", "risk_zone"}
            assert warning["severity"] in {"moderate", "high"}


def test_customer_with_no_drivers_is_skipped_and_reported(db_session, world):
    summary, _ = _seed_and_reload(db_session, now=datetime.now(timezone.utc))

    customer_c_id = world["customer_c"].customer_id
    assert summary.skipped == {customer_c_id: "no drivers in this customer's fleet"}
    assert customer_c_id not in summary.created
    assert db_session.query(RoutePlan).filter_by(customer_id=customer_c_id).count() == 0


def test_requested_customer_is_included_beyond_the_default_count(db_session, world):
    customer_a_id = world["customer_a"].customer_id
    customer_b_id = world["customer_b"].customer_id

    summary, _ = _seed_and_reload(
        db_session, now=datetime.now(timezone.utc), customer_ids=[customer_b_id], customer_count=1
    )

    assert set(summary.created) == {customer_a_id, customer_b_id}


def test_unknown_requested_customer_is_skipped_not_fatal(db_session, world):
    summary, _ = _seed_and_reload(
        db_session, now=datetime.now(timezone.utc), customer_ids=[999999]
    )

    assert summary.skipped[999999] == "no such customer"
    assert summary.total_created > 0


def test_rerunning_adds_another_batch_instead_of_erroring(db_session, world):
    now = datetime.now(timezone.utc)
    first = seed_demo_routes(db_session, rng=random.Random(1), now=now)
    db_session.commit()
    second = seed_demo_routes(db_session, rng=random.Random(2), now=now)
    db_session.commit()

    assert db_session.query(RoutePlan).count() == first.total_created + second.total_created


def test_raises_when_no_support_agent_exists(db_session):
    customer = _make_customer(db_session, tag="SOLO")
    _make_driver(db_session, customer=customer, tag="SOLO")

    with pytest.raises(DemoSeedError, match="SupportAgent"):
        seed_demo_routes(db_session, rng=random.Random(1))


def test_seeded_routes_show_up_with_driver_names_in_the_chat_summary(db_session, world):
    _, rows = _seed_and_reload(db_session, now=datetime.now(timezone.utc))
    customer_a_id = world["customer_a"].customer_id

    summary_text = summarize_todays_routes(db_session, customer_id=customer_a_id)

    a_rows = [r for r in rows if r.customer_id == customer_a_id]
    assert f"{len(a_rows)} route(s) planned today" in summary_text
    for row in a_rows:
        if row.driver_id is None:
            assert "driver: Unassigned" in summary_text
        else:
            driver = db_session.get(Driver, row.driver_id)
            assert f"driver: {driver.full_name}" in summary_text


def test_main_commits_and_prints_a_summary(engine, db_session, world, monkeypatch, capsys):
    customer_c_id = world["customer_c"].customer_id
    db_session.close()
    monkeypatch.setattr(seed_module, "SessionLocal", sessionmaker(bind=engine))

    seed_module.main(["--seed", "3"])

    out = capsys.readouterr().out
    assert "Demo routes created" in out
    assert "Skipped customers:" in out
    assert f"customer {customer_c_id}: no drivers in this customer's fleet" in out
    with Session(engine) as fresh:
        assert fresh.query(RoutePlan).count() > 0


def test_main_exits_non_zero_without_a_support_agent(engine, db_session, monkeypatch, capsys):
    customer = _make_customer(db_session, tag="SOLO")
    _make_driver(db_session, customer=customer, tag="SOLO")
    db_session.close()
    monkeypatch.setattr(seed_module, "SessionLocal", sessionmaker(bind=engine))

    with pytest.raises(SystemExit) as exc_info:
        seed_module.main([])

    assert exc_info.value.code == 1
    assert "Demo route seeding aborted" in capsys.readouterr().out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_seed_demo_routes.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'app.seed.seed_demo_routes'` — all 12 tests error.

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/seed/seed_demo_routes.py`:

```python
"""Demo route data: ``python -m app.seed.seed_demo_routes``.

**This script generates SYNTHETIC DEMO DATA for exercising the chat and
report surfaces. It is not real incident detection, and not a placeholder
for it.** No GPS or incident-detection hardware exists anywhere in this
codebase (all telematics data is synthetic/seeded -- see
app/seed/generator.py). The route "problems" written here are drawn at
random from a fixed pool of realistic-sounding warnings, so that a customer
asking "are there any problems with my route?" -- or a support agent asking
for today's overview -- has something to look at. See
docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md.

Run it manually, once (it is never run per question). Each run inserts a
fresh batch of `RoutePlan` rows dated *today* in Sydney-local time (via
app.timeutil) for a handful of existing customers -- by default the first
DEFAULT_CUSTOMER_COUNT by id, plus any passed with ``--customer-id`` (e.g.
whichever customer you are demoing as). For each customer it writes a mix
of `active` and `completed` routes, assigns a random driver from THAT
customer's own fleet (leaving a minority "Unassigned"), and attributes the
dispatch to the first `SupportAgent` (`created_by_role="support_agent"`).
Customers with no drivers are skipped and reported, not fatal. Re-running
adds another batch rather than erroring -- idempotent by convention, not by
constraint, since "today" may span several demo runs.

No network and no API key: origin/destination labels come from the fixed
Sydney place list below (approximate real landmark coordinates),
`geometry` is a plain 2-point GeoJSON LineString between them (enough for
the live map to draw a line and interpolate a position -- not a real routed
polyline), and distance/duration are fixed per pair, lightly jittered. This
is the second deliberate home of hardcoded real-world geography, alongside
app/seed/generator.py's DEMO_CORRIDORS; the spec above is what authorizes
it.

Every warning dict has exactly the shape `save_route_plan` stores for a
real `app.ai.route_planning.Warning` (`location`, `distance_from_origin_km`,
`type`, `severity`, `description`), so reports, chat, the Routes page and
the live map treat seeded warnings identically to real risk-pipeline
output, with no format branching anywhere else.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.customer import Customer
from app.models.enums import RoutePlanStatus
from app.models.route_plan import RoutePlan
from app.models.support_agent import SupportAgent
from app.models.telematics import Driver
from app.timeutil import SITE_TZ, site_day_bounds


@dataclass(frozen=True)
class DemoPlace:
    label: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class DemoRoutePair:
    origin: DemoPlace
    destination: DemoPlace
    #: Typical driving distance/time for this pair -- fixed, not computed.
    distance_km: float
    duration_min: float


_SYDNEY_CBD = DemoPlace("Sydney CBD", -33.8688, 151.2093)
_PARRAMATTA = DemoPlace("Parramatta", -33.8150, 151.0011)
_BONDI_BEACH = DemoPlace("Bondi Beach", -33.8908, 151.2743)
_SYDNEY_AIRPORT = DemoPlace("Sydney Airport", -33.9399, 151.1753)
_CHATSWOOD = DemoPlace("Chatswood", -33.7969, 151.1803)
_MACQUARIE_PARK = DemoPlace("Macquarie Park", -33.7757, 151.1245)
_PORT_BOTANY = DemoPlace("Port Botany", -33.9725, 151.2173)
_LIVERPOOL = DemoPlace("Liverpool", -33.9200, 150.9238)
_NORTH_SYDNEY = DemoPlace("North Sydney", -33.8390, 151.2070)
_MANLY = DemoPlace("Manly", -33.7969, 151.2840)
_PENRITH = DemoPlace("Penrith", -33.7507, 150.6877)
_BLACKTOWN = DemoPlace("Blacktown", -33.7710, 150.9063)
_HORNSBY = DemoPlace("Hornsby", -33.7025, 151.0990)
_BANKSTOWN = DemoPlace("Bankstown", -33.9181, 151.0350)
_CRONULLA = DemoPlace("Cronulla", -34.0572, 151.1522)

DEMO_ROUTE_PAIRS: tuple[DemoRoutePair, ...] = (
    DemoRoutePair(_SYDNEY_CBD, _PARRAMATTA, 24.1, 35.0),
    DemoRoutePair(_BONDI_BEACH, _SYDNEY_AIRPORT, 13.5, 25.0),
    DemoRoutePair(_CHATSWOOD, _MACQUARIE_PARK, 8.2, 15.0),
    DemoRoutePair(_PORT_BOTANY, _LIVERPOOL, 31.0, 38.0),
    DemoRoutePair(_NORTH_SYDNEY, _MANLY, 12.4, 24.0),
    DemoRoutePair(_PARRAMATTA, _PENRITH, 30.5, 30.0),
    DemoRoutePair(_BLACKTOWN, _HORNSBY, 29.8, 36.0),
    DemoRoutePair(_BANKSTOWN, _CRONULLA, 22.6, 33.0),
    DemoRoutePair(_SYDNEY_CBD, _CHATSWOOD, 10.9, 20.0),
    DemoRoutePair(_SYDNEY_AIRPORT, _BANKSTOWN, 17.3, 26.0),
)

#: `(type, severity, description)` -- descriptions phrased exactly the way
#: `evaluate_weather_warnings`/`evaluate_risk_zone_warnings` phrase real ones.
DEMO_WARNING_POOL: tuple[tuple[str, str, str], ...] = (
    ("weather", "high", "Heavy rain forecast near this segment (85% probability)."),
    ("weather", "moderate", "Heavy rain forecast near this segment (65% probability)."),
    ("weather", "moderate", "Strong winds forecast near this segment (48 km/h)."),
    ("weather", "moderate", "Low visibility forecast near this segment (700m)."),
    (
        "risk_zone",
        "high",
        "16 driving events recorded within 500m of this point (7 harsh braking).",
    ),
    (
        "risk_zone",
        "high",
        "14 driving events recorded within 500m of this point (8 speeding).",
    ),
    (
        "risk_zone",
        "moderate",
        "11 driving events recorded within 500m of this point (5 speeding).",
    ),
    (
        "risk_zone",
        "moderate",
        "12 driving events recorded within 500m of this point (6 route deviation).",
    ),
)

DEFAULT_CUSTOMER_COUNT = 3
MIN_ROUTES_PER_CUSTOMER = 3
MAX_ROUTES_PER_CUSTOMER = 5
MAX_WARNINGS_PER_ROUTE = 3
UNASSIGNED_DRIVER_PROBABILITY = 0.2
COMPLETED_PROBABILITY = 0.35


class DemoSeedError(RuntimeError):
    """The database isn't in a state this script can seed into."""


@dataclass
class DemoRouteSummary:
    #: customer_id -> {"active": n, "completed": n}
    created: dict[int, dict[str, int]] = field(default_factory=dict)
    #: customer_id -> why it was skipped
    skipped: dict[int, str] = field(default_factory=dict)

    @property
    def total_created(self) -> int:
        return sum(counts["active"] + counts["completed"] for counts in self.created.values())


def _choose_customers(
    db: Session, customer_ids: list[int] | None, customer_count: int
) -> tuple[list[Customer], dict[int, str]]:
    """Explicitly requested customers first (unknown ids are skipped, not
    fatal), then the first `customer_count` customers by id, de-duplicated."""
    chosen: list[Customer] = []
    skipped: dict[int, str] = {}
    seen: set[int] = set()

    for customer_id in customer_ids or []:
        if customer_id in seen:
            continue
        seen.add(customer_id)
        customer = db.get(Customer, customer_id)
        if customer is None:
            skipped[customer_id] = "no such customer"
            continue
        chosen.append(customer)

    for customer in db.query(Customer).order_by(Customer.customer_id).limit(customer_count).all():
        if customer.customer_id in seen:
            continue
        seen.add(customer.customer_id)
        chosen.append(customer)

    return chosen, skipped


def _point_along(pair: DemoRoutePair, fraction: float) -> tuple[float, float]:
    """(latitude, longitude) `fraction` of the way along the straight line
    between the pair's endpoints -- the same 2-point line stored as the
    row's geometry, so a warning marker always sits on the drawn route."""
    latitude = pair.origin.latitude + (pair.destination.latitude - pair.origin.latitude) * fraction
    longitude = pair.origin.longitude + (pair.destination.longitude - pair.origin.longitude) * fraction
    return latitude, longitude


def _demo_warnings(rng: random.Random, pair: DemoRoutePair, distance_km: float) -> list[dict]:
    count = rng.randint(0, MAX_WARNINGS_PER_ROUTE)
    warnings: list[dict] = []
    for warning_type, severity, description in rng.sample(DEMO_WARNING_POOL, count):
        fraction = rng.uniform(0.1, 0.9)
        latitude, longitude = _point_along(pair, fraction)
        warnings.append(
            {
                "location": {"lat": round(latitude, 6), "lon": round(longitude, 6)},
                "distance_from_origin_km": round(distance_km * fraction, 2),
                "type": warning_type,
                "severity": severity,
                "description": description,
            }
        )
    warnings.sort(key=lambda warning: warning["distance_from_origin_km"])
    return warnings


def _statuses_for_one_customer(rng: random.Random) -> list[RoutePlanStatus]:
    """At least one active and one completed route per customer (so every
    demo customer shows a real mix), then a random mix for the rest."""
    count = rng.randint(MIN_ROUTES_PER_CUSTOMER, MAX_ROUTES_PER_CUSTOMER)
    statuses = [RoutePlanStatus.ACTIVE, RoutePlanStatus.COMPLETED]
    statuses += [
        RoutePlanStatus.COMPLETED if rng.random() < COMPLETED_PROBABILITY else RoutePlanStatus.ACTIVE
        for _ in range(count - len(statuses))
    ]
    return statuses


def _demo_route_plan(
    rng: random.Random,
    *,
    customer: Customer,
    drivers: list[Driver],
    agent: SupportAgent,
    status: RoutePlanStatus,
    now: datetime,
    day_start: datetime,
) -> RoutePlan:
    pair = rng.choice(DEMO_ROUTE_PAIRS)
    distance_km = round(pair.distance_km * rng.uniform(0.95, 1.1), 2)
    duration_min = round(pair.duration_min * rng.uniform(0.9, 1.3), 2)

    # Every timestamp is clamped into [day_start, now]: always "today" in
    # Sydney, never in the future.
    if status == RoutePlanStatus.COMPLETED:
        created_at = max(day_start, now - timedelta(minutes=duration_min + rng.uniform(10, 180)))
        completed_at = min(now, created_at + timedelta(minutes=duration_min))
    else:
        # Somewhere between just-departed and 90% of the way there, so the
        # live map shows vehicles genuinely mid-route.
        created_at = max(day_start, now - timedelta(minutes=rng.uniform(0, duration_min * 0.9)))
        completed_at = None

    driver_id = (
        None if rng.random() < UNASSIGNED_DRIVER_PROBABILITY else rng.choice(drivers).driver_id
    )

    return RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="support_agent",
        created_by_id=agent.support_agent_id,
        driver_id=driver_id,
        origin_label=pair.origin.label,
        destination_label=pair.destination.label,
        distance_km=distance_km,
        duration_min=duration_min,
        geometry={
            "type": "LineString",
            # GeoJSON order is [longitude, latitude].
            "coordinates": [
                [pair.origin.longitude, pair.origin.latitude],
                [pair.destination.longitude, pair.destination.latitude],
            ],
        },
        warnings=_demo_warnings(rng, pair, distance_km),
        unavailable=False,
        unavailable_reason=None,
        status=status,
        created_at=created_at,
        completed_at=completed_at,
    )


def seed_demo_routes(
    db: Session,
    *,
    rng: random.Random,
    customer_ids: list[int] | None = None,
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    now: datetime | None = None,
) -> DemoRouteSummary:
    """Insert one batch of synthetic today-dated `RoutePlan` rows (see
    module docstring). Flushes, never commits -- `run()` owns the commit.

    Raises DemoSeedError when there is no `SupportAgent` row to attribute
    the simulated dispatch to (`RoutePlan.created_by_id` is NOT NULL)."""
    now = now if now is not None else datetime.now(timezone.utc)
    day_start, _ = site_day_bounds(now.astimezone(SITE_TZ).date())

    agent = db.query(SupportAgent).order_by(SupportAgent.support_agent_id).first()
    if agent is None:
        raise DemoSeedError(
            "No SupportAgent row exists to attribute the demo dispatch to -- "
            "run `python -m app.seed.seed` first."
        )

    customers, skipped = _choose_customers(db, customer_ids, customer_count)
    summary = DemoRouteSummary(skipped=skipped)

    for customer in customers:
        drivers = (
            db.query(Driver)
            .filter(Driver.customer_id == customer.customer_id)
            .order_by(Driver.driver_id)
            .all()
        )
        if not drivers:
            summary.skipped[customer.customer_id] = "no drivers in this customer's fleet"
            continue

        counts = {"active": 0, "completed": 0}
        for status in _statuses_for_one_customer(rng):
            db.add(
                _demo_route_plan(
                    rng,
                    customer=customer,
                    drivers=drivers,
                    agent=agent,
                    status=status,
                    now=now,
                    day_start=day_start,
                )
            )
            counts[status.value] += 1
        summary.created[customer.customer_id] = counts

    db.flush()
    return summary


def _print_summary(summary: DemoRouteSummary) -> None:
    print("Demo routes created (SYNTHETIC demo data -- not real incident detection):")
    if not summary.created:
        print("  (none)")
    for customer_id, counts in sorted(summary.created.items()):
        print(
            f"  customer {customer_id}: "
            f"{counts['active']} active, {counts['completed']} completed"
        )
    print(f"  total: {summary.total_created}")
    if summary.skipped:
        print("Skipped customers:")
        for customer_id, reason in sorted(summary.skipped.items()):
            print(f"  customer {customer_id}: {reason}")


def run(
    *,
    customer_ids: list[int] | None = None,
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    seed: int | None = None,
) -> DemoRouteSummary:
    """Seed one batch into the configured database, commit, print a summary.
    Same session/commit/rollback shape as `app.seed.seed.run`."""
    session = SessionLocal()
    try:
        summary = seed_demo_routes(
            session,
            rng=random.Random(seed),
            customer_ids=customer_ids,
            customer_count=customer_count,
        )
        session.commit()
        _print_summary(summary)
        return summary
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed.seed_demo_routes",
        description=(
            "Insert a batch of SYNTHETIC today-dated demo routes (with random "
            "warnings and drivers) for testing the chat/report surfaces."
        ),
    )
    parser.add_argument(
        "--customer-id",
        dest="customer_ids",
        type=int,
        action="append",
        default=None,
        help="Always include this customer (repeatable) -- e.g. the one you are demoing as.",
    )
    parser.add_argument(
        "--customers",
        dest="customer_count",
        type=int,
        default=DEFAULT_CUSTOMER_COUNT,
        help=f"Also include the first N customers by id (default {DEFAULT_CUSTOMER_COUNT}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed, for a reproducible batch (default: random).",
    )
    args = parser.parse_args(argv)

    try:
        run(customer_ids=args.customer_ids, customer_count=args.customer_count, seed=args.seed)
    except DemoSeedError as exc:
        print(f"Demo route seeding aborted: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/test_seed_demo_routes.py -v`

Expected: all 12 tests pass. Then `cd backend && pytest -q` — whole suite passes.

- [ ] **Step 5: Commit**

```bash
git add backend/app/seed/seed_demo_routes.py backend/tests/test_seed_demo_routes.py
git commit -m "Add a manual seed script for synthetic today-dated demo routes"
```

---

## Task 8: Documentation

**Dispatch:** Mechanical — verbatim insertions. A fast/cheap implementer is sufficient; reviewer checks each insertion landed at its anchor and no existing paragraph was dropped.

**Files:**
- Modify: `docs/DEPLOYMENT.md` (insert before `## Route-planning feature setup`, line 115; insert before `## Known pitfalls (found deploying this exact repo)`, line 155)
- Modify: `docs/API_REFERENCE.md` (`### POST /chat` response + intent paragraph, lines 109-129; feedback bullet, lines 155-157; new section before `### POST /chat/sessions/{chat_session_id}/survey`, line 163)
- Modify: `docs/SECURITY.md` (append to the "Fixed vulnerability" section after line 152; audit bullet lines 192-193; new section before `## HTTPS / TLS`, line 207)
- Modify: `docs/RAG_PIPELINE.md` (mermaid line 27; `## Step 5` section lines 135-153; `## Atomicity` first paragraph lines 157-163)
- Modify: `docs/ARCHITECTURE.md` (step 6, lines 139-142)
- Modify: `docs/ROUTE_PLANNING.md` (`### Chat: "today's routes" intent` paragraphs at lines 367-378, 405-416; `## Testing` line containing `` `test_chat_api.py` (intent routing). Frontend:``)
- Modify: `docs/README.md` (lines 35-38)

**Interfaces:**
- Consumes: every name produced by Tasks 1-7 (`SMTP_*`/`ESCALATION_EMAIL_TO`, `send_escalation_email`, `handle_answer(chat_answer)`, `escalation_offered`, `POST /chat/messages/{id}/escalate` → `{support_ticket_id, email_sent}`, `route_plan_driver_label`, `python -m app.seed.seed_demo_routes --customer-id/--customers/--seed`).
- Produces: documentation only; nothing consumes it.

- [ ] **Step 1: Write the failing test**

No test. Documentation prose has no executable assertion in this repo, and inventing one (e.g. grepping docs for a string) would test the grep, not the docs. Verification is Step 2's/Step 4's read-back check instead.

- [ ] **Step 2: Run test to verify it fails**

Run (from the repository root): `grep -n "escalation_offered\|seed_demo_routes\|SMTP_HOST" docs/*.md`

Expected: no output at all (exit status 1) — no doc mentions opt-in escalation, the demo seed script, or SMTP yet.

- [ ] **Step 3: Write minimal implementation**

**`docs/DEPLOYMENT.md`** — insert immediately before the line `## Route-planning feature setup`:

````markdown
### Demo route data (optional, manual)

```bash
docker compose exec backend python -m app.seed.seed_demo_routes
```

Adds one batch of **synthetic** `RoutePlan` rows dated *today* (Sydney
time), so "are there any problems with my route?", "give me a route
overview", the daily reports, the Routes page and the Live Tracking map all
have something to show. This is demo scaffolding, **not** incident
detection — the warnings are drawn at random from a fixed pool of
realistic-sounding weather/risk-zone entries (see the module docstring in
`backend/app/seed/seed_demo_routes.py`).

- Run it **after** `python -m app.seed.seed`: it needs existing customers,
  drivers, and at least one `SupportAgent` (the simulated dispatcher —
  `created_by_role="support_agent"`). With no support agent it prints
  `Demo route seeding aborted: ...` and exits `1`.
- By default it seeds the first 3 customers by id. Add
  `--customer-id N` (repeatable) to always include the customer you're
  demoing as, `--customers N` to change the default count, and `--seed N`
  for a reproducible batch.
- Each customer gets 3–5 routes between fixed Sydney place pairs (e.g.
  Sydney CBD → Parramatta, Bondi Beach → Sydney Airport), at least one
  `active` and one `completed`, a random driver from that customer's own
  fleet (a minority left "Unassigned"), and 0–3 warnings each.
- A customer with no drivers is skipped and listed under
  `Skipped customers:` in the printed summary — not fatal.
- No network access and no `ORS_API_KEY` needed: geometry is a straight
  2-point line between the place pair, and distance/duration are fixed per
  pair.
- Re-running adds **another** batch; it never errors on existing rows.
  Seeded rows stop counting as "today" at Sydney midnight like any other
  route plan, so re-run it on each demo day.

````

**`docs/DEPLOYMENT.md`** — insert immediately before the line `## Known pitfalls (found deploying this exact repo)`:

````markdown
## Escalation email setup

When the AI can't confidently answer, the chat widget asks the customer
"Would you like to escalate this to a human?". On **Yes**,
`POST /chat/messages/{id}/escalate` always creates a support ticket, then
sends one email to `ESCALATION_EMAIL_TO` (`backend/app/integrations/email.py`,
stdlib `smtplib` — no extra dependency). Email is optional: with
`SMTP_HOST` blank the ticket is still created and the widget shows
"(email notification could not be sent)".

| Variable | Default | Purpose |
|---|---|---|
| `SMTP_HOST` | *(blank)* | SMTP server. Blank = email disabled |
| `SMTP_PORT` | `587` | Submission port |
| `SMTP_USERNAME` | *(blank)* | Login user; blank skips `login()` (e.g. an open local relay) |
| `SMTP_PASSWORD` | *(blank)* | Login password — `.env` only, never logged |
| `SMTP_FROM_ADDRESS` | *(blank)* | `From:` address; falls back to `SMTP_USERNAME` |
| `SMTP_USE_TLS` | `true` | STARTTLS after connecting. Only set `false` for a local test relay |
| `ESCALATION_EMAIL_TO` | `CIHE241731@student.edu.cihe.au` | The one inbox every escalation goes to |

Like `ORS_API_KEY`, every one of these must also be listed in
`docker-compose.yml`'s `backend.environment` block (it already is) — a
variable set only in `.env` never reaches the container.

To verify delivery end-to-end with your real SMTP settings:

```bash
docker compose exec backend python3 -c "
from app.integrations.email import send_escalation_email
send_escalation_email(customer_name='Test Customer', customer_email='test@example.test', question='Test question', answer='Test answer', support_ticket_id=0)
print('sent')
"
```

A failure raises `EmailNotConfiguredError` (something blank) or
`EmailDeliveryError` (with the SMTP server's own error message — e.g. an
authentication rejection). The API never retries a failed email; the
ticket in `GET /tickets` is the durable record.

````

**`docs/API_REFERENCE.md`** — in `### POST /chat`, replace everything from the line `**Response** (\`ChatResponse\`) \`200\`` through the end of the numbered list item `2. **Reports** ...` (the line `   unconditionally.`) with:

````markdown
**Response** (`ChatResponse`) `200`
```json
{
  "session_id": 1, "message_id": 42, "answer": "...",
  "confidence": 0.82, "escalated": false, "escalation_offered": false,
  "route_plan": null
}
```

- `escalation_offered` — `true` only on a low-confidence RAG answer:
  `answer` is then the fixed fallback text, and the client should ask
  "Would you like to escalate this to a human?". **Nothing has been
  escalated yet** — the customer's "Yes" is
  `POST /chat/messages/{chat_message_id}/escalate` (below).
- `escalated` — "a ticket now exists for this exchange". Always `false` on
  `POST /chat` since escalation became opt-in; the escalate and feedback
  endpoints report their own outcome.

Before the RAG pipeline runs, the query is checked for three other intents,
in order:

1. **Route planning** — "plan a trip from Sydney CBD to Parramatta" routes
   to the [route-planning feature](ROUTE_PLANNING.md) instead of RAG,
   returning a populated `route_plan` field (same shape as
   [`POST /route-plan`](#post-route-plan)'s response) on success — `null`
   on every other kind of turn.
2. **Today's routes** — a route word (`route`/`routes`) plus one of
   `today`, `active`, `problem(s)`, `issue(s)`, `overview` (and no
   mention of `deviation`) returns a deterministic summary of the chat
   session's own customer's routes planned today, naming each route's
   driver. See [ROUTE_PLANNING.md](ROUTE_PLANNING.md).
3. **Reports** (see [RAG_PIPELINE.md](RAG_PIPELINE.md)) — phrases like
   "daily report" or "start of day" route straight to the report
   generators.

All three return `confidence: 1.0, escalated: false,
escalation_offered: false` unconditionally.
````

**`docs/API_REFERENCE.md`** — in `### PATCH /chat/messages/{chat_message_id}/feedback`, replace:

```markdown
- `feedback: false` (thumbs-down) creates a `SupportTicket` — idempotently:
  pressing thumbs-down twice, or thumbs-down on a session already
  auto-escalated for low confidence, reuses the existing ticket rather than
  erroring (`ChatSession 1 → 0..1 SupportTicket` is a DB unique constraint).
```

with:

```markdown
- `feedback: false` (thumbs-down) creates a `SupportTicket` — idempotently:
  pressing thumbs-down twice, or thumbs-down on a session the customer
  already escalated via `POST /chat/messages/{chat_message_id}/escalate`,
  reuses the existing ticket rather than erroring (`ChatSession 1 → 0..1
  SupportTicket` is a DB unique constraint).
```

**`docs/API_REFERENCE.md`** — insert immediately before the line `### \`POST /chat/sessions/{chat_session_id}/survey\``:

````markdown
### `POST /chat/messages/{chat_message_id}/escalate`

The customer's explicit **Yes** to an escalation offer
(`ChatResponse.escalation_offered: true`). No request body.

**Response** (`ChatMessageEscalateResponse`) `200`
```json
{ "support_ticket_id": 7, "email_sent": true }
```

- Ownership is exactly `PATCH .../feedback`'s: `404` if the message
  doesn't exist **or** (for a `customer` caller) belongs to another
  customer's session — deliberately indistinguishable — and `400` if it
  isn't a `role=assistant` message. `support_agent` is unrestricted.
- Creates the session's `SupportTicket` plus an `in_app` `Notification`
  and **commits them first**. Idempotent: a double-click, a second "Yes" in
  the same session, or a session that already has a thumbs-down ticket
  reuses the existing ticket (and sends no second notification).
- **Then** best-effort sends one email to the operator-configured
  `ESCALATION_EMAIL_TO` with the customer's name, their question (the user
  message immediately before this answer), the answer text, and the
  ticket id — see [DEPLOYMENT.md](DEPLOYMENT.md#escalation-email-setup).
  An email is attempted on every successful call, including a reused
  ticket.
- `email_sent: false` means the ticket exists but the email wasn't
  delivered (SMTP not configured, or the SMTP server failed). A failed
  email is never a `5xx` and never rolls the ticket back.

````

**`docs/SECURITY.md`** — insert immediately after the paragraph ending `customer instead, never leave it fleet-wide.` (the end of the "General lesson for this codebase" paragraph):

```markdown

**Driver names follow the same rule.** The "today's routes" answer now
names who was driving each route. Those names are looked up only from the
driver ids on the already session-scoped route rows (one batched query in
`summarize_todays_routes`), so they can never name another customer's
driver — and there is still deliberately no fleet-wide variant of this
chat answer. A support agent who wants "who was driving" across every
customer uses `GET /route-plans/live` (which returns `driver_name` straight
to the caller and never writes it into a tenant-owned row).
```

**`docs/SECURITY.md`** — replace:

```markdown
- `action=chat_answer` — one row per `POST /chat` turn, noting confidence
  and whether it was escalated.
```

with:

```markdown
- `action=chat_answer` — one row per `POST /chat` turn, noting confidence,
  `escalated` (always `False` on this path since escalation became opt-in)
  and `escalation_offered` (whether the customer was offered a human
  hand-off). The customer's later "Yes"
  (`POST /chat/messages/{id}/escalate`) is recorded by the `SupportTicket`
  it creates, not by a separate audit row.
```

**`docs/SECURITY.md`** — insert immediately before the line `## HTTPS / TLS`:

```markdown
## Opt-in escalation email

Escalation to a human is **opt-in**: a low-confidence answer only
*offers* it, and nothing is created or sent until the customer says yes
(`POST /chat/messages/{chat_message_id}/escalate`). That endpoint's
ownership check is identical to the thumbs-down feedback endpoint's (`404`
for another customer's message, indistinguishable from a missing one), so
it grants no capability thumbs-down didn't already have.

What leaves the system on a "Yes": one plaintext email to the single
operator-configured `ESCALATION_EMAIL_TO` address, containing the
customer's full name and email (both **encrypted at rest** in the database
— see above — but necessarily decrypted to put in the email), their
question, the fallback answer, and the ticket id. Everything in it comes
from that customer's own chat session. The destination is one operator
setting, never customer-supplied.

- Transport: STARTTLS when `SMTP_USE_TLS=true` (the default). Setting it
  `false` sends in cleartext — only for a local test relay.
- `SMTP_PASSWORD` comes from `.env` only, is handed straight to
  `smtplib.login()`, and is never logged or included in an exception
  message (`backend/tests/test_email.py` pins this).
- Ticket creation and email sending are separate failure domains: the
  ticket is committed first, and an email failure only yields
  `email_sent: false` — it can't roll back or hide the ticket.
- Not rate-limited (POC scope): an authenticated customer can trigger one
  email per "Yes" on their own messages. The widget disables "Yes" while a
  request is in flight.

```

**`docs/RAG_PIPELINE.md`** — in the mermaid block, replace:

```
    ESC -- no --> FB["FALLBACK_TEXT +\nauto-created SupportTicket"]
```

with:

```
    ESC -- no --> FB["FALLBACK_TEXT +\nescalation_offered=true\n(no ticket until the customer says yes)"]
```

**`docs/RAG_PIPELINE.md`** — replace the whole `## Step 5: escalation gate (\`app/ai/escalation.py\`)` section (from its heading through the paragraph ending `see\n[API_REFERENCE.md](API_REFERENCE.md).`) with:

```markdown
## Step 5: escalation offer (`app/ai/escalation.py`)

`handle_answer(chat_answer)` compares `confidence` against
`ESCALATION_CONFIDENCE_THRESHOLD` (env-configurable, default **0.6**):

- **At or above threshold**: the LLM's answer is returned to the customer
  unchanged, `escalation_offered: false`.
- **Below threshold**: the customer-facing text is replaced with the exact
  `FALLBACK_TEXT`, and the response carries `escalation_offered: true`.
  **Nothing else happens** — `handle_answer` no longer takes a database
  session at all, so it cannot create a ticket. The chat widget asks
  "Would you like to escalate this to a human?"; **No** is purely local,
  **Yes** calls `POST /chat/messages/{id}/escalate`.

That endpoint creates the `SupportTicket` + in-app `Notification` through
`get_or_create_escalation_ticket` — idempotent per session (a
`SELECT`-first check, with an `IntegrityError`-safe retry under a
SAVEPOINT for the concurrent-request case), so a double-click can never
create two tickets — commits, and only **then** best-effort emails the
support inbox (see [DEPLOYMENT.md](DEPLOYMENT.md#escalation-email-setup)).
An email failure returns `email_sent: false`; the ticket stays.

`escalated` on `POST /chat`'s response is therefore always `false` now; it
still means "a ticket exists for this exchange" on the escalate and
feedback endpoints' responses.

Separately, a customer can also escalate any *specific* answer by giving
it a thumbs-down (`PATCH /chat/messages/{id}/feedback`), which creates or
reuses the same one-per-session ticket — see
[API_REFERENCE.md](API_REFERENCE.md).
```

**`docs/RAG_PIPELINE.md`** — replace the first paragraph of `## Atomicity`:

```markdown
Everything in one `POST /chat` call — session creation/lookup, the two
`ChatMessage` rows (user + assistant), any escalation `SupportTicket`/
`Notification`, and the `AuditLog` row — is staged with `db.flush()` and
committed **exactly once**, at the very end of the request. This closes a
real bug where each piece used to commit separately: a failure between (say)
the ticket commit and the message commit could strand a `SupportTicket`
pointing at a session with zero messages for a support agent to act on.
```

with:

```markdown
Everything in one `POST /chat` call — session creation/lookup, the two
`ChatMessage` rows (user + assistant), and the `AuditLog` row — is staged
with `db.flush()` and committed **exactly once**, at the very end of the
request. This closed a real bug from when each piece (including the
then-automatic escalation ticket) committed separately: a failure between
two commits could strand a `SupportTicket` pointing at a session with zero
messages. Escalation tickets are now created only by
`POST /chat/messages/{id}/escalate`, which likewise commits its ticket and
notification together, once, before attempting the email.
```

**`docs/ARCHITECTURE.md`** — replace:

```markdown
6. `ai/escalation.py` checks that confidence against
   `ESCALATION_CONFIDENCE_THRESHOLD` (default `0.6`). Below it, the answer
   is replaced with a fixed fallback string and a `SupportTicket` is
   auto-created; at or above it, the LLM's answer is returned as-is.
```

with:

```markdown
6. `ai/escalation.py` checks that confidence against
   `ESCALATION_CONFIDENCE_THRESHOLD` (default `0.6`). Below it, the answer
   is replaced with a fixed fallback string and the response carries
   `escalation_offered: true` — the widget then asks the customer whether
   to escalate, and only a "Yes" (`POST /chat/messages/{id}/escalate`)
   creates a `SupportTicket` and emails the support inbox. At or above the
   threshold, the LLM's answer is returned as-is.
```

**`docs/ROUTE_PLANNING.md`** — in `### Chat: "today's routes" intent`, replace the first paragraph (from `` `app/api/chat.py`'s `_detect_todays_routes_intent` recognizes questions`` through `intent (above) didn't match first.`) with:

```markdown
`app/api/chat.py`'s `_detect_todays_routes_intent` recognizes questions
about routes **already** planned, rather than a request to plan a new one
— e.g. "what routes were used today", "active routes", "are there any
problems with my route?", "give me a route overview". It requires both a
route word (`route`/`routes`) **and** a signal word (`today`, `active`,
`problem`/`problems`, `issue`/`issues`, `overview`). Bare `risk`/`risks`
were deliberately dropped from the signal words (final review): this app
has real domain vocabulary built on "risk" — `DrivingEventType.
ROUTE_DEVIATION`, the seeded "Route deviation alerts explained" KB article
— so an ordinary question like "are route deviations a risk to my fleet?"
used to collide with this intent and get hijacked away from RAG. Adding
"problem"/"issue" reopened the same collision ("are route deviations an
issue for my fleet?"), so any query mentioning `deviation` is excluded
from this intent outright. All of these are pinned by regression tests in
`backend/tests/test_chat_api.py`. It's only checked when the route-plan
intent (above) didn't match first.
```

Replace the paragraph beginning `If no routes were planned that day, the answer is the fixed string` (through `call-out when any warning is \`severity="high"\`).`) with:

```markdown
If no routes were planned that day, the answer is the fixed string `"No
routes have been planned today."`. Otherwise it opens with a count (`"N
route(s) planned today (X active, Y completed)."`) followed by one line per
route naming origin/destination, status, and **who was driving** — e.g.
`- Sydney CBD -> Parramatta (active, driver: Alice Driver): 2 warning(s),
1 high-severity` — and either "route data was unavailable when planned" or
a warning count (with a high-severity call-out when any warning is
`severity="high"`). Unassigned routes read `driver: Unassigned`. Driver
names come from one batched `Driver` query per answer, only for driver ids
on the already-scoped rows (`route_plan_driver_label` in
`app/ai/route_planning.py`, shared with the reports' route-risk section,
which names drivers the same way using the driver map it already loads).

To give this answer something to show in a demo, run the synthetic
demo-route seed script — see
[DEPLOYMENT.md](DEPLOYMENT.md#demo-route-data-optional-manual).
```

Replace:

```markdown
Like the route-plan intent, this bypasses RAG/escalation entirely, always
returns `confidence: 1.0, escalated: false`, and writes the same
```

with:

```markdown
Like the route-plan intent, this bypasses RAG/escalation entirely, always
returns `confidence: 1.0, escalated: false, escalation_offered: false`,
and writes the same
```

In `## Testing`, replace the line:

```markdown
fields), `test_chat_api.py` (intent routing). Frontend:
```

with:

```markdown
fields), `test_summarize_todays_routes.py` (driver names and the one
batched driver query), `test_seed_demo_routes.py` (every seeded demo row
round-trips through the real model and `WarningOut`), `test_chat_api.py`
(intent routing, including the widened "today's routes" words and their
false-positive guards). Frontend:
```

**`docs/README.md`** — replace:

```markdown
  data, escalating to a human support ticket whenever it isn't confident in
  its own answer.
```

with:

```markdown
  data. Whenever it isn't confident in its own answer it shows a fixed
  fallback message and *offers* to escalate to a human — a support ticket
  (plus an email to the support inbox, if SMTP is configured) is created
  only if the customer says yes.
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repository root): `grep -c "escalation_offered\|seed_demo_routes\|SMTP_HOST" docs/DEPLOYMENT.md docs/API_REFERENCE.md docs/SECURITY.md docs/RAG_PIPELINE.md docs/ARCHITECTURE.md docs/ROUTE_PLANNING.md`

Expected: a non-zero count for every file, and each insertion reads back as written above at its anchor. Then run both full suites one last time to confirm nothing regressed: `cd backend && pytest -q` (all tests pass) and `cd frontend && npx vitest run` (all tests pass).

- [ ] **Step 5: Commit**

```bash
git add docs/DEPLOYMENT.md docs/API_REFERENCE.md docs/SECURITY.md docs/RAG_PIPELINE.md docs/ARCHITECTURE.md docs/ROUTE_PLANNING.md docs/README.md
git commit -m "Document demo route seeding, driver-aware summaries, and opt-in escalation email"
```
