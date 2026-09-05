from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from aegis.agents import AgentRuntime, RecordingTool, StepStatus, ToolRegistry
from aegis.governance import ActionType, GovernanceGate, RestrictedDomain, TenantPolicy
from aegis.hr.workflows import ONBOARDING, TALENT_ACQUISITION
from aegis.ledger import DecisionLedger
from aegis.persistence import (
    ApiKeyRepository,
    Database,
    LedgerRepository,
    PolicyRepository,
    RunRepository,
)

TENANT = "66666666-6666-6666-6666-666666666666"
OTHER = "77777777-7777-7777-7777-777777777777"


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite+pysqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def make_run(policy: TenantPolicy | None = None):
    tools = ToolRegistry()
    tools.register(RecordingTool(frozenset(ActionType), output={"ok": True}))
    runtime = AgentRuntime(
        gate=GovernanceGate(policy or TenantPolicy.conservative(TENANT)),
        tools=tools,
        ledger=DecisionLedger(),
    )
    run = runtime.start(TALENT_ACQUISITION, UUID(TENANT), "candidate-42")
    runtime.advance(run)
    return runtime, run


class TestRunPersistence:
    def test_a_run_survives_a_restart(self, database: Database) -> None:
        _, run = make_run()

        with database.session() as session:
            RunRepository(session).save(run)

        with database.session() as session:
            restored = RunRepository(session).load(TENANT, str(run.run_id))

        assert restored is not None
        assert restored.run_id == run.run_id
        assert restored.subject_id == "candidate-42"
        assert restored.definition.name == "talent_acquisition"

    def test_step_statuses_and_reasons_are_preserved(self, database: Database) -> None:
        _, run = make_run()

        with database.session() as session:
            RunRepository(session).save(run)
        with database.session() as session:
            restored = RunRepository(session).load(TENANT, str(run.run_id))

        assert restored is not None
        assert restored.state("source").status is StepStatus.COMPLETED
        assert restored.state("shortlist").status is StepStatus.AWAITING_APPROVAL
        assert restored.state("shortlist").reasons

    def test_a_restored_run_can_be_advanced_further(self, database: Database) -> None:
        policy = TenantPolicy(
            tenant_id=TENANT,
            autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
        )
        runtime, run = make_run(policy)

        with database.session() as session:
            RunRepository(session).save(run)
        with database.session() as session:
            restored = RunRepository(session).load(TENANT, str(run.run_id))

        assert restored is not None
        runtime.approve(restored, "offer", approver="hr.partner@example.com")
        assert restored.state("offer").status is StepStatus.COMPLETED

    def test_saving_twice_updates_rather_than_duplicates(self, database: Database) -> None:
        runtime, run = make_run(
            TenantPolicy(
                tenant_id=TENANT,
                autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
            )
        )

        with database.session() as session:
            RunRepository(session).save(run)
        runtime.approve(run, "offer", approver="hr.partner@example.com")
        with database.session() as session:
            RunRepository(session).save(run)

        with database.session() as session:
            restored = RunRepository(session).load(TENANT, str(run.run_id))

        assert restored is not None
        assert restored.state("offer").approver == "hr.partner@example.com"
        assert len(restored.steps) == len(TALENT_ACQUISITION.steps)

    def test_another_tenant_cannot_load_the_run(self, database: Database) -> None:
        _, run = make_run()

        with database.session() as session:
            RunRepository(session).save(run)
        with database.session() as session:
            assert RunRepository(session).load(OTHER, str(run.run_id)) is None

    def test_an_unknown_run_is_none_not_an_error(self, database: Database) -> None:
        with database.session() as session:
            assert RunRepository(session).load(TENANT, str(uuid4())) is None

    def test_runs_can_be_listed_for_a_subject(self, database: Database) -> None:
        with database.session() as session:
            repository = RunRepository(session)
            for _ in range(2):
                _, run = make_run()
                repository.save(run)

        with database.session() as session:
            assert len(RunRepository(session).for_subject(TENANT, "candidate-42")) == 2

    def test_context_accumulated_during_a_run_is_persisted(self, database: Database) -> None:
        tools = ToolRegistry()
        tools.register(RecordingTool(frozenset(ActionType), output={"verdict": "CLEAR"}))
        runtime = AgentRuntime(
            gate=GovernanceGate(
                TenantPolicy(tenant_id=TENANT, autonomous_actions=frozenset(ActionType))
            ),
            tools=tools,
        )
        run = runtime.start(ONBOARDING, UUID(TENANT), "employee-9", context={"role": "engineer"})
        runtime.advance(run)

        with database.session() as session:
            RunRepository(session).save(run)
        with database.session() as session:
            restored = RunRepository(session).load(TENANT, str(run.run_id))

        assert restored is not None
        assert restored.context["role"] == "engineer"


class TestLedgerPersistence:
    def test_entries_persist_and_verify_intact(self, database: Database) -> None:
        with database.session() as session:
            repository = LedgerRepository(session)
            ledger = DecisionLedger()
            for index in range(4):
                entry = ledger.append(
                    tenant_id=TENANT,
                    workflow="talent_acquisition",
                    run_id="run-1",
                    step=f"step_{index}",
                    action_type="SCORE_CANDIDATE",
                    subject_id="candidate-42",
                    agent="sourcing-agent",
                    outcome="COMPLETED",
                )
                repository.append(TENANT, entry)

        with database.session() as session:
            report = LedgerRepository(session).verify(TENANT)

        assert report.intact
        assert report.entries_checked == 4

    def test_the_head_survives_a_restart_so_the_chain_continues(
        self, database: Database
    ) -> None:
        ledger = DecisionLedger()
        with database.session() as session:
            repository = LedgerRepository(session)
            repository.append(
                TENANT,
                ledger.append(
                    tenant_id=TENANT,
                    workflow="onboarding",
                    run_id="run-1",
                    step="request_documents",
                    action_type="REQUEST_DOCUMENT",
                    subject_id="employee-1",
                    agent="documentation-agent",
                    outcome="COMPLETED",
                ),
            )

        with database.session() as session:
            sequence, head = LedgerRepository(session).head(TENANT)

        assert sequence == 1
        assert head != "0" * 64

    def test_a_fresh_tenant_starts_at_genesis(self, database: Database) -> None:
        with database.session() as session:
            sequence, head = LedgerRepository(session).head(OTHER)

        assert sequence == 0
        assert head == "0" * 64

    def test_one_tenants_entries_are_invisible_to_another(self, database: Database) -> None:
        ledger = DecisionLedger()
        with database.session() as session:
            LedgerRepository(session).append(
                TENANT,
                ledger.append(
                    tenant_id=TENANT,
                    workflow="onboarding",
                    run_id="run-1",
                    step="verify",
                    action_type="VERIFY_DOCUMENT",
                    subject_id="employee-1",
                    agent="documentation-agent",
                    outcome="COMPLETED",
                ),
            )

        with database.session() as session:
            assert LedgerRepository(session).entries(OTHER) == ()


class TestPolicyPersistence:
    def test_a_tenant_policy_round_trips(self, database: Database) -> None:
        policy = TenantPolicy(
            tenant_id=TENANT,
            autonomous_actions=frozenset({ActionType.SEND_MESSAGE, ActionType.SCORE_CANDIDATE}),
            forbidden_actions=frozenset({ActionType.REJECT_APPLICATION}),
            readable_domains=frozenset({RestrictedDomain.COMPENSATION_BAND}),
            confidence_floor=0.85,
        )

        with database.session() as session:
            PolicyRepository(session).upsert(TENANT, "Acme Ltd", policy)
        with database.session() as session:
            restored = PolicyRepository(session).load(TENANT)

        assert restored is not None
        assert restored.autonomous_actions == policy.autonomous_actions
        assert restored.forbidden_actions == policy.forbidden_actions
        assert restored.readable_domains == policy.readable_domains
        assert restored.confidence_floor == pytest.approx(0.85)

    def test_an_unconfigured_tenant_loads_as_none(self, database: Database) -> None:
        with database.session() as session:
            assert PolicyRepository(session).load(OTHER) is None

    def test_updating_a_policy_replaces_rather_than_duplicates(
        self, database: Database
    ) -> None:
        with database.session() as session:
            repository = PolicyRepository(session)
            repository.upsert(TENANT, "Acme", TenantPolicy.conservative(TENANT))
            repository.upsert(TENANT, "Acme Renamed", TenantPolicy.permissive(TENANT))

        with database.session() as session:
            restored = PolicyRepository(session).load(TENANT)

        assert restored is not None
        assert ActionType.SCHEDULE_INTERVIEW in restored.autonomous_actions


class TestApiKeyPersistence:
    def test_an_issued_key_resolves_to_its_tenant(self, database: Database) -> None:
        with database.session() as session:
            ApiKeyRepository(session).issue(TENANT, "ci", "hash-1", ["ADMIN"])

        with database.session() as session:
            row = ApiKeyRepository(session).resolve("hash-1")

        assert row is not None
        assert row.tenant_id == TENANT
        assert row.roles == ["ADMIN"]

    def test_a_revoked_key_stops_resolving(self, database: Database) -> None:
        with database.session() as session:
            repository = ApiKeyRepository(session)
            repository.issue(TENANT, "ci", "hash-2")
            assert repository.revoke("hash-2")

        with database.session() as session:
            assert ApiKeyRepository(session).resolve("hash-2") is None

    def test_revoking_an_unknown_key_reports_false(self, database: Database) -> None:
        with database.session() as session:
            assert not ApiKeyRepository(session).revoke("missing")

    def test_an_unknown_key_resolves_to_none(self, database: Database) -> None:
        with database.session() as session:
            assert ApiKeyRepository(session).resolve("nope") is None
