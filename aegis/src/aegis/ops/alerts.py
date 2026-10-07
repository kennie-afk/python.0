"""Alert delivery: email and webhook, from an outbox in the database.

An alert is written to the `alerts` table first and delivered after, so an unreachable mail server
or webhook delays it instead of losing it. Each pending alert is retried up to five times; a
delivered one is stamped and never sent again. With neither channel configured alerts simply stay
queued where the console can show them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request

from aegis.integrations.email import EmailError, EmailTransport, SentEmail
from aegis.persistence.models import AlertRow
from aegis.persistence.repositories import AlertRepository
from aegis.persistence.session import Database

logger = logging.getLogger("aegis.alerts")

MAX_ATTEMPTS = 5


class AlertDispatcher:
    def __init__(
        self,
        database: Database,
        email: EmailTransport | None = None,
        recipients: list[str] | None = None,
        webhook: str | None = None,
        webhook_secret: str | None = None,
    ) -> None:
        if webhook and not webhook.startswith(("http://", "https://")):
            raise ValueError("AEGIS_ALERT_WEBHOOK must be an http(s) URL")
        self._database = database
        self._email = email
        self._recipients = recipients or []
        self._webhook = webhook
        self._secret = webhook_secret

    @classmethod
    def from_environment(cls, database: Database, email: EmailTransport) -> AlertDispatcher:
        recipients = [
            item.strip()
            for item in os.environ.get("AEGIS_ALERT_EMAIL_TO", "").split(",")
            if item.strip()
        ]
        return cls(
            database,
            email=email,
            recipients=recipients,
            webhook=os.environ.get("AEGIS_ALERT_WEBHOOK") or None,
            webhook_secret=os.environ.get("AEGIS_ALERT_WEBHOOK_SECRET") or None,
        )

    @property
    def configured(self) -> bool:
        return bool(self._webhook or (self._email and self._recipients))

    def deliver_pending(self, tenant_id: str) -> int:
        """Try every pending alert for a tenant once; returns how many were delivered."""
        if not self.configured:
            return 0
        delivered = 0
        with self._database.session(tenant_id) as session:
            repository = AlertRepository(session)
            for row in repository.pending(tenant_id, MAX_ATTEMPTS):
                error = self._send(row)
                repository.mark(row, error)
                delivered += int(error is None)
        return delivered

    def _send(self, row: AlertRow) -> str | None:
        failures: list[str] = []
        if self._webhook:
            body = json.dumps(
                {
                    "source": "aegis",
                    "tenant_id": row.tenant_id,
                    "kind": row.kind,
                    "message": row.message,
                    "at": row.created_at.isoformat(),
                    "payload": row.payload,
                },
                sort_keys=True,
            ).encode()
            headers = {"Content-Type": "application/json"}
            if self._secret:
                headers["X-Aegis-Signature"] = hmac.new(
                    self._secret.encode(), body, hashlib.sha256
                ).hexdigest()
            request = urllib.request.Request(
                self._webhook, data=body, headers=headers
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    if not 200 <= response.status < 300:
                        failures.append(f"webhook answered {response.status}")
            except (urllib.error.URLError, OSError) as error:
                failures.append(f"webhook: {error}")
        if self._email:
            for recipient in self._recipients:
                try:
                    self._email.deliver(
                        SentEmail(
                            to=recipient,
                            subject=f"[Aegis] {row.kind.replace('_', ' ')}",
                            body=row.message,
                        )
                    )
                except EmailError as error:
                    failures.append(f"email to {recipient}: {error}")
        return "; ".join(failures) or None
