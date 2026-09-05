from __future__ import annotations

import os
import re
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

from aegis.agents.tools import ToolResult
from aegis.governance.actions import ActionType, ProposedAction

ADDRESS = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


class EmailError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SentEmail:
    to: str
    subject: str
    body: str


class EmailTransport(Protocol):
    def deliver(self, message: SentEmail) -> str: ...


@dataclass
class MockEmailTransport:
    sent: list[SentEmail] = field(default_factory=list)

    def deliver(self, message: SentEmail) -> str:
        self.sent.append(message)
        return f"mock-{len(self.sent)}"


class SmtpEmailTransport:
    def __init__(
        self,
        host: str,
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        sender: str = "no-reply@aegis.local",
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._sender = sender

    @classmethod
    def from_environment(cls) -> SmtpEmailTransport:
        host = os.environ.get("AEGIS_SMTP_HOST")
        if not host:
            raise EmailError(
                "AEGIS_SMTP_HOST is not set; the platform uses the mock transport "
                "unless real delivery is configured"
            )
        return cls(
            host=host,
            port=int(os.environ.get("AEGIS_SMTP_PORT", "587")),
            username=os.environ.get("AEGIS_SMTP_USERNAME"),
            password=os.environ.get("AEGIS_SMTP_PASSWORD"),
            use_tls=os.environ.get("AEGIS_SMTP_TLS", "true").lower() != "false",
            sender=os.environ.get("AEGIS_SMTP_SENDER", "no-reply@aegis.local"),
        )

    def deliver(self, message: SentEmail) -> str:
        email = EmailMessage()
        email["From"] = self._sender
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)

        try:
            with smtplib.SMTP(self._host, self._port, timeout=20) as server:
                if self._use_tls:
                    server.starttls()
                if self._username and self._password:
                    server.login(self._username, self._password)
                server.send_message(email)
        except (smtplib.SMTPException, OSError) as error:
            raise EmailError(f"delivery to {message.to} failed: {error}") from error

        return email["Message-ID"] or f"smtp-{abs(hash(message.body))}"


class EmailTool:
    def __init__(self, transport: EmailTransport) -> None:
        self._transport = transport

    def handles(self) -> frozenset[ActionType]:
        return frozenset({ActionType.SEND_MESSAGE})

    def execute(self, action: ProposedAction) -> ToolResult:
        recipient = str(action.payload.get("recipient_email", "")).strip()
        if not ADDRESS.match(recipient):
            return ToolResult.failed(
                f"refusing to send: {recipient!r} is not a deliverable address"
            )

        subject = str(action.payload.get("subject", "")).strip()
        body = str(action.payload.get("body", "")).strip()
        if not subject or not body:
            return ToolResult.failed("refusing to send an email with no subject or no body")

        try:
            reference = self._transport.deliver(SentEmail(to=recipient, subject=subject, body=body))
        except EmailError as error:
            return ToolResult.failed(str(error))

        return ToolResult.ok(email_reference=reference, email_recipient=recipient)
