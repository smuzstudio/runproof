"""Delivery of a failed check to a human being.

This module is the answer to the fourth question. Liveness, throughput and
effect are all answered from data; ownership is not a property of the data
at all. It is the question of whether the answer reaches someone, and no
query can establish it.

Two rules shape everything here:

    1. A failed delivery is never silent. If a profile declares an alert
       destination and the message does not leave, `runproof` exits
       non-zero and says so on stderr. An alerter that fails quietly is
       strictly worse than no alerter, because it converts a loud failure
       into a silent one — the exact substitution this tool exists to
       prevent.

    2. Credentials never appear in the profile. The profile is committed to
       a repository and shown to prospects; the password is not. Everything
       secret comes from the environment, and its absence is an error
       rather than a default.

Provenance: written from a blank file for Smuz. Standard library `smtplib`
and `email.message`; no dependency and no model.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal

from .model import Report
from .report import to_text

Trigger = Literal["failure", "always"]

#: Environment variables carrying the transport credentials. Named with a
#: prefix so that runproof's mailbox can be a different one from the mailbox
#: of whatever agent it is verifying — the alerter should not share a
#: failure domain with the subject where it can be avoided.
ENV_HOST = "RUNPROOF_SMTP_HOST"
ENV_PORT = "RUNPROOF_SMTP_PORT"
ENV_USER = "RUNPROOF_SMTP_USER"
ENV_PASSWORD = "RUNPROOF_SMTP_PASSWORD"


class AlertError(RuntimeError):
    """The alert could not be delivered. Always fatal, never a warning."""


@dataclass(frozen=True)
class AlertConfig:
    """The `[alert]` block of a profile.

    `owner` is a required field for the same reason `catches` is required on
    an assertion: a destination with no named human is a mailing list that
    everyone assumes someone else reads.
    """

    to: tuple[str, ...]
    owner: str
    subject_prefix: str = "[runproof]"
    on: Trigger = "failure"
    from_addr: str = ""
    timeout_seconds: float = 20.0


def _credentials() -> tuple[str, int, str, str]:
    host = os.getenv(ENV_HOST, "").strip()
    user = os.getenv(ENV_USER, "").strip()
    password = os.getenv(ENV_PASSWORD, "")
    missing = [
        name for name, value in ((ENV_HOST, host), (ENV_USER, user),
                                 (ENV_PASSWORD, password))
        if not value
    ]
    if missing:
        # Deliberately an error and not a skip. A profile that asks for
        # alerting on a machine that cannot send is a misconfiguration, and
        # the only moment it is cheap to discover is now.
        raise AlertError(
            "alerting is configured but the transport is not: missing "
            + ", ".join(missing)
        )
    raw_port = os.getenv(ENV_PORT, "587").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise AlertError(f"{ENV_PORT}={raw_port!r} is not a port number") from exc
    return host, port, user, password


def send(config: AlertConfig, subject: str, body: str) -> str:
    """Deliver one message, or raise `AlertError`. Returns the destination."""
    host, port, user, password = _credentials()
    sender = config.from_addr or user

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(config.to)
    message["Subject"] = f"{config.subject_prefix} {subject}".strip()
    message.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=config.timeout_seconds) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise AlertError(f"{type(exc).__name__}: {exc}") from exc
    return ", ".join(config.to)


def should_alert(config: AlertConfig, report: Report) -> bool:
    return config.on == "always" or not report.passed


def subject_for(report: Report) -> str:
    if report.passed:
        return f"{report.subject}: verified"
    n = len(report.failures)
    return f"{report.subject}: NOT VERIFIABLE — {n} assertion{'s' if n != 1 else ''} unmet"


def body_for(report: Report, profile_path: str = "") -> str:
    lines = [to_text(report), ""]
    if profile_path:
        lines.append(f"profile: {profile_path}")
    lines.append(
        "This message was sent by runproof because a check did not hold. "
        "Nothing was changed; the subject database is opened read-only."
    )
    return "\n".join(lines)


def deliver(config: AlertConfig, report: Report, profile_path: str = "") -> str:
    return send(config, subject_for(report), body_for(report, profile_path))
