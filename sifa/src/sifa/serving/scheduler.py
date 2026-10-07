"""Background duties: deliver alerts, watch live drift, optionally retrain on a schedule.

`tick()` does one pass and is what the tests drive; `start()` runs it on a thread. Alerts are
written to the outbox in the state store first and delivered from there, so a webhook that is
down delays an alert instead of losing it (five attempts, then it stays in the outbox, visible in
the export). Retraining is off unless an interval is configured, and it only ever starts a
canary: it still has to pass the guard to go live.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from sifa.serving.platform import Platform

log = logging.getLogger("sifa.scheduler")


class Scheduler:
    def __init__(
        self,
        platform: Platform,
        webhook: str | None = None,
        interval: float = 30.0,
        retrain_every: float = 0.0,
    ) -> None:
        if webhook and not webhook.startswith(("http://", "https://")):
            raise ValueError("SIFA_ALERT_WEBHOOK must be an http(s) URL")
        self.platform = platform
        self.webhook = webhook
        self.interval = interval
        self.retrain_every = retrain_every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._drift_key: tuple[str, ...] = ()

    def tick(self) -> dict[str, int]:
        queued = self._check_drift()
        retrained = self._maybe_retrain()
        delivered = self.deliver()
        return {"queued": queued, "retrained": int(retrained), "delivered": delivered}

    def _check_drift(self) -> int:
        report = self.platform.live_drift()
        drifted = tuple(sorted(r.feature for r in report["reports"] if r.drifted))
        if drifted and drifted != self._drift_key:
            self.platform.enqueue_alert(
                "drift",
                f"live serving traffic has drifted from the training reference on "
                f"{', '.join(drifted)}",
                {"features": list(drifted), "rows": report["rows"]},
            )
            self._drift_key = drifted
            return 1
        if not drifted:
            self._drift_key = ()
        return 0

    def _maybe_retrain(self) -> bool:
        if self.retrain_every <= 0:
            return False
        platform = self.platform
        last = float(platform.store.get("last_retrain", 0.0))
        if time.time() - last < self.retrain_every:
            return False
        if platform.registry.canary("ranker") is not None:
            return False  # one canary at a time; try again next tick
        outcome = platform.promote_candidate(actor="system:scheduler")
        platform.store.put("last_retrain", time.time())
        platform.enqueue_alert(
            "retrain",
            f"scheduled retrain started {outcome['version'].label} as a canary",
            {"version": outcome["version"].version, "auc": outcome["auc"]},
        )
        return True

    def deliver(self) -> int:
        if not self.webhook:
            return 0
        delivered = 0
        for alert in self.platform.store.pending_alerts():
            body = json.dumps(
                {"source": "sifa", "kind": alert["kind"], "message": alert["message"],
                 "at": alert["at"], "payload": alert["payload"]}
            ).encode()
            request = urllib.request.Request(
                self.webhook, data=body, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    ok = 200 <= response.status < 300
            except (urllib.error.URLError, OSError) as error:
                log.warning("alert %s not delivered: %s", alert["id"], error)
                ok = False
            self.platform.store.mark_alert(
                alert["id"], datetime.now(UTC).isoformat() if ok else None
            )
            delivered += int(ok)
        return delivered

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="sifa-scheduler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
