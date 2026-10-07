from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aegis.api.app import Platform, app, get_platform
from aegis.bias.adverse_impact import GroupOutcome, ImpactVerdict, four_fifths_test
from aegis.demo.data import age_band, build_candidates, build_cohort, simulate_screening
from aegis.demo.seed import DEMO_APPROVERS, DemoSeeder
from aegis.persistence import Database, purge_tenant
from aegis.persistence.repositories import LedgerRepository

TENANT = "5a5a5a5a-5a5a-4a5a-8a5a-5a5a5a5a5a5a"


def _verdict(candidates, advanced, key):  # type: ignore[no-untyped-def]
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for candidate, yes in zip(candidates, advanced, strict=True):
        totals[key(candidate)][0] += int(yes)
        totals[key(candidate)][1] += 1
    return four_fifths_test(
        [GroupOutcome(group=g, selected=s, total=n) for g, (s, n) in totals.items()]
    )


def test_pool_is_reproducible_and_carries_the_planted_age_disparity() -> None:
    assert build_candidates() == build_candidates()
    candidates = build_candidates()
    advanced = simulate_screening(candidates)

    age = _verdict(candidates, advanced, lambda c: c.age_group)
    gender = _verdict(candidates, advanced, lambda c: c.gender)

    assert age.verdict is ImpactVerdict.ADVERSE_IMPACT
    assert [g.group for g in age.groups if g.adversely_impacted] == ["45 and over"]
    assert gender.verdict is ImpactVerdict.NO_ADVERSE_IMPACT


def test_removing_the_proxy_signal_clears_the_finding() -> None:
    candidates = build_candidates()
    advanced = simulate_screening(candidates, drop=("recent_tooling",))
    assert _verdict(candidates, advanced, lambda c: c.age_group).verdict is (
        ImpactVerdict.NO_ADVERSE_IMPACT
    )


def test_protected_attributes_never_reach_the_model() -> None:
    from aegis.anonymization.engine import PROTECTED_ATTRIBUTES

    candidate = build_candidates(count=1)[0]
    assert PROTECTED_ATTRIBUTES & set(candidate.record)  # present in the source record
    assert age_band(candidate.age) in {"Under 30", "30 to 44", "45 and over"}


def test_cohort_is_learnable() -> None:
    history, current = build_cohort()
    leavers = sum(e.left for e in history)
    assert 0.2 < leavers / len(history) < 0.6  # a real mix of outcomes, or training is refused
    assert len(current) == 64
    assert len({e.subject_key for e in history + current}) == len(history) + len(current)


@pytest.fixture(scope="module")
def seeded() -> Iterator[tuple[TestClient, Platform]]:
    database = Database("sqlite+pysqlite:///:memory:")
    platform = Platform(database=database)
    app.dependency_overrides[get_platform] = lambda: platform
    token = platform.tokens.mint(TENANT, "demo", frozenset({"ADMIN"}))
    approvers = {
        name: platform.tokens.mint(TENANT, name, frozenset({"APPROVER"})) for name in DEMO_APPROVERS
    }
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        DemoSeeder(client, say=lambda _: None, approver_tokens=approvers).run()
        yield client, platform
    app.dependency_overrides.clear()
    database.dispose()


def test_demo_fills_every_console_screen(seeded: tuple[TestClient, Platform]) -> None:
    client, _ = seeded
    overview = client.get("/v1/overview").json()

    assert overview["runs"] >= 20
    assert overview["awaiting_approval"] >= 8  # people have real decisions waiting
    assert overview["failed"] >= 2  # and something genuinely failed
    assert overview["awaiting_external"] >= 1
    assert overview["screenings"]["ADVANCE"] > 50
    assert overview["model_trained"] is True
    assert sum(overview["retention_bands"].values()) == 64
    assert overview["impact_reports"]["ADVERSE_IMPACT"] >= 1
    assert overview["impact_reports"]["NO_ADVERSE_IMPACT"] >= 2
    assert overview["impact_reports"]["INSUFFICIENT_DATA"] == 1
    assert overview["human_decisions"] > 0


def test_the_flag_reaches_the_audit_chain_and_the_chain_holds(
    seeded: tuple[TestClient, Platform],
) -> None:
    client, _ = seeded
    flagged = client.get("/v1/ledger/search", params={"outcome": "FLAGGED"}).json()
    assert len(flagged) == 1
    assert flagged[0]["workflow"] == "compliance"

    reports = client.get("/v1/bias/reports", params={"verdict": "ADVERSE_IMPACT"}).json()
    assert reports[0]["ledger_sequence"] == flagged[0]["sequence"]

    assert client.get("/v1/ledger/verify").json()["intact"] is True


def test_a_failed_step_can_be_recovered_and_one_is_left_for_a_person(
    seeded: tuple[TestClient, Platform],
) -> None:
    client, _ = seeded
    failed = client.get("/v1/runs", params={"needs": "failed", "limit": 100}).json()
    engage = [
        run
        for run in failed
        if any(s["key"] == "engage" and s["status"] == "FAILED" for s in run["steps"])
    ]
    assert len(engage) == 1
    assert next(s for s in engage[0]["steps"] if s["key"] == "engage")["retryable"] is True


def test_reset_removes_the_tenant_cleanly(seeded: tuple[TestClient, Platform]) -> None:
    _, platform = seeded
    with platform.database.session() as session:
        assert LedgerRepository(session).counts(TENANT)["total"] > 0
        purge_tenant(session, TENANT)
    with platform.database.session() as session:
        assert LedgerRepository(session).counts(TENANT) == {"total": 0, "approvals": 0}
