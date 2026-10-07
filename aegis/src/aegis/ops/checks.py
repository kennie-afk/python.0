"""Scheduled checks, run per tenant: ledger integrity, drift of live scoring inputs, retrain due.

These need no new data from anyone. The drift check compares the feature values the active model
actually scored recently (kept in the append-only score history) with the reference distribution
stored with that model version. A retrain is only ever *recommended*: Aegis does not hold outcome
labels, so it cannot retrain by itself, and the alert says so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

from aegis.attrition.features import FEATURE_NAMES
from aegis.ops.alerts import AlertDispatcher
from aegis.persistence.models import TenantRow
from aegis.persistence.repositories import (
    AlertRepository,
    LedgerRepository,
    ModelIntegrityError,
    ModelRepository,
    RiskScoreRepository,
)
from aegis.persistence.session import Database
from aegis.security.signing import is_production, ledger_signing_key
from aegis.verification.drift import DriftSeverity
from aegis.verification.model_gate import feature_drift

# PSI on fewer rows than this is mostly sampling noise and would raise false alarms.
MIN_LIVE_ROWS = 200


@dataclass(slots=True)
class CheckSummary:
    tenant_id: str
    alerts_queued: list[str] = field(default_factory=list)
    delivered: int = 0
    notes: list[str] = field(default_factory=list)


def tenant_ids(database: Database) -> list[str]:
    from sqlalchemy import select

    with database.session() as session:
        return [str(t) for t in session.scalars(select(TenantRow.tenant_id))]


def _raise(
    database: Database,
    tenant: str,
    summary: CheckSummary,
    kind: str,
    message: str,
    payload: dict[str, object],
    dedupe: str,
) -> None:
    with database.session(tenant) as session:
        row = AlertRepository(session).add(tenant, kind, message, payload, dedupe_key=dedupe)
    if row is not None:
        summary.alerts_queued.append(kind)


def _clear(database: Database, tenant: str, dedupe: str) -> None:
    with database.session(tenant) as session:
        AlertRepository(session).clear_dedupe(tenant, dedupe)


def run_checks(
    database: Database,
    tenant: str,
    dispatcher: AlertDispatcher | None = None,
    now: datetime | None = None,
    live_window_days: int = 30,
    retrain_after_days: int | None = None,
) -> CheckSummary:
    moment = now or datetime.now(UTC)
    max_age = retrain_after_days or int(os.environ.get("AEGIS_RETRAIN_MAX_AGE_DAYS", "90"))
    summary = CheckSummary(tenant_id=tenant)

    key = ledger_signing_key()
    with database.session(tenant) as session:
        integrity = LedgerRepository(session).verify(
            tenant, signing_key=key, require_signed=is_production()
        )
    if not integrity.intact:
        _raise(
            database,
            tenant,
            summary,
            "ledger_integrity",
            f"the audit trail failed verification at entry {integrity.broken_at}: "
            f"{integrity.reason}",
            {"broken_at": integrity.broken_at, "reason": integrity.reason},
            "ledger-broken",
        )
    else:
        _clear(database, tenant, "ledger-broken")

    with database.session(tenant) as session:
        active = ModelRepository(session).active(tenant)
        version = active.version if active else None
        trained_at = active.created_at if active else None
        try:
            model = ModelRepository(session).load(tenant) if active else None
        except ModelIntegrityError as error:
            model = None
            _raise(
                database,
                tenant,
                summary,
                "model_integrity",
                f"the active attrition model failed its signature check: {error}",
                {"version": version},
                f"model-integrity:{version}",
            )
        live = RiskScoreRepository(session).recent_features(
            tenant, moment - timedelta(days=live_window_days)
        )

    drifted = False
    if model is not None and version is not None:
        rows = [[row.get(name) for name in FEATURE_NAMES] for row in live if row]
        usable = [r for r in rows if all(v is not None for v in r)]
        if len(usable) >= MIN_LIVE_ROWS and len(model.reference) > 0:
            reports = feature_drift(model.reference, np.asarray(usable, dtype=float))
            worst = [r for r in reports if r.severity is DriftSeverity.SIGNIFICANT]
            drifted = bool(worst)
            if worst:
                names = ", ".join(r.feature for r in worst)
                _raise(
                    database,
                    tenant,
                    summary,
                    "drift",
                    f"inputs scored in the last {live_window_days} days have drifted from model "
                    f"v{version}'s training reference on: {names}",
                    {
                        "version": version,
                        "features": [r.feature for r in worst],
                        "rows": len(usable),
                    },
                    f"drift:{version}",
                )
            else:
                _clear(database, tenant, f"drift:{version}")
        else:
            summary.notes.append("not enough recent scoring (or no reference) to check drift")

        age_days = (moment - (trained_at or moment).astimezone(UTC)).days if trained_at else 0
        if drifted or age_days >= max_age:
            why = "scoring inputs have drifted" if drifted else f"it is {age_days} days old"
            _raise(
                database,
                tenant,
                summary,
                "retrain_recommended",
                f"retraining model v{version} is recommended: {why}. Aegis holds no outcome "
                "labels, so a person must supply new training data.",
                {"version": version, "drifted": drifted, "age_days": age_days},
                f"retrain:{version}",
            )

    if dispatcher is not None:
        summary.delivered = dispatcher.deliver_pending(tenant)
    return summary
