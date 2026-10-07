"""Fixtures for the governance, verification, key-lifecycle and evidence tests.

`gov` is a platform on in-memory SQLite plus a client and a way to sign in as any role of any
tenant. `cohort` builds attrition training data with variation in every feature (so drift can be
measured) and an optional shift of the whole population.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform, token_failures
from aegis.persistence import Database

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@dataclass
class Gov:
    platform: Platform
    client: TestClient

    def headers(
        self, *roles: str, tenant: str = TENANT, subject: str = "person@example.com"
    ) -> dict[str, str]:
        token = self.platform.tokens.mint(tenant, subject, frozenset(roles))
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def gov() -> Iterator[Gov]:
    database = Database("sqlite+pysqlite:///:memory:")
    platform = Platform(database=database)
    app.dependency_overrides[get_platform] = lambda: platform
    token_failures.reset()
    with TestClient(app) as client:
        yield Gov(platform, client)
    app.dependency_overrides.clear()
    database.dispose()


def _employee(rng: random.Random, index: int, leaving: bool, shift: float) -> dict[str, Any]:
    return {
        "subject_key": f"emp_{index}",
        "tenure_years": rng.uniform(0.5, 10) * shift,
        "months_since_promotion": (rng.uniform(30, 60) if leaving else rng.uniform(1, 12)) * shift,
        "salary": (rng.uniform(70_000, 85_000) if leaving else rng.uniform(100_000, 120_000))
        * shift,
        "band_midpoint": 100_000.0,
        "peer_median_salary": 100_000.0,
        "manager_changes_24m": int((3 if leaving else 0) + rng.randint(0, 2) + 8 * (shift - 1)),
        "commute_minutes": rng.uniform(10, 60) * shift,
        "engagement_score": min(5.0, rng.uniform(1, 2.5) if leaving else rng.uniform(3, 5)),
        "training_hours_12m": rng.uniform(0, 40) * shift,
        "overtime_hours_monthly": rng.uniform(0, 20) * shift,
        "internal_applications_12m": int(
            (3 if leaving else 0) + rng.randint(0, 1) + 6 * (shift - 1)
        ),
    }


@pytest.fixture
def cohort() -> Callable[..., tuple[list[dict[str, Any]], list[bool]]]:
    def build(
        size: int = 160, shift: float = 1.0, seed: int = 5
    ) -> tuple[list[dict[str, Any]], list[bool]]:
        rng = random.Random(seed)
        left = [index % 2 == 0 for index in range(size)]
        return [_employee(rng, i, flag, shift) for i, flag in enumerate(left)], left

    return build


@pytest.fixture
def signed_gov(monkeypatch: pytest.MonkeyPatch) -> Iterator[Gov]:
    """Like `gov`, with ledger signing on (the key is held outside the database)."""
    monkeypatch.setenv("AEGIS_LEDGER_SIGNING_KEY", "ledger-signing-key-for-tests-0123456789")
    database = Database("sqlite+pysqlite:///:memory:")
    platform = Platform(database=database)
    app.dependency_overrides[get_platform] = lambda: platform
    with TestClient(app) as client:
        yield Gov(platform, client)
    app.dependency_overrides.clear()
    database.dispose()
