from __future__ import annotations

from uuid import UUID

import pytest

from aegis.agents import (
    AgentRuntime,
    ApprovalError,
    FailingTool,
    RecordingTool,
    RunStatus,
    StepStatus,
    ToolRegistry,
)
from aegis.governance import ActionType, GovernanceGate, TenantPolicy
from aegis.hr.workflows import ONBOARDING, TALENT_ACQUISITION
from aegis.ledger import DecisionLedger

TENANT = UUID("22222222-2222-2222-2222-222222222222")


def registry(*, failing: frozenset[ActionType] = frozenset()) -> ToolRegistry:
    tools = ToolRegistry()
    all_types = frozenset(ActionType)
    working = all_types - failing
    tools.register(RecordingTool(working, output={"ok": True}))
    if failing:
        tools.register(FailingTool(failing))
    return tools


CONTEXT = {
    "recipient_email": "candidate@example.com",
    "subject": "Interview invitation",
    "body": "Are you available on Thursday?",
    "attendees": ["interviewer@example.com"],
    "starts_at": "2099-01-01T09:00:00+00:00",
}


def runtime(
    policy: TenantPolicy | None = None,
    tools: ToolRegistry | None = None,
    ledger: DecisionLedger | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        gate=GovernanceGate(policy or TenantPolicy.conservative(str(TENANT))),
        tools=tools or registry(),
        ledger=ledger,
    )


class TestTalentAcquisition:
    def test_the_pipeline_runs_autonomously_up_to_the_offer(self) -> None:
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
        )
        engine = runtime(policy)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        assert run.state("source").status is StepStatus.COMPLETED
        assert run.state("engage").status is StepStatus.COMPLETED
        assert run.state("schedule").status is StepStatus.COMPLETED

    def test_the_offer_halts_the_pipeline_for_a_human(self) -> None:
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
        )
        engine = runtime(policy)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        assert run.state("offer").status is StepStatus.AWAITING_APPROVAL
        assert run.status is RunStatus.BLOCKED
        assert engine.pending_approvals(run) == ("offer",)

    def test_shortlisting_is_not_delegated_under_a_conservative_policy(self) -> None:
        engine = runtime()
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        assert run.state("shortlist").status is StepStatus.AWAITING_APPROVAL
        assert run.state("engage").status is StepStatus.PENDING

    def test_approving_the_offer_completes_the_workflow(self) -> None:
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
        )
        engine = runtime(policy)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))
        engine.advance(run)

        engine.approve(run, "offer", approver="hr.partner@example.com")

        assert run.state("offer").status is StepStatus.COMPLETED
        assert run.state("offer").approver == "hr.partner@example.com"
        assert run.status is RunStatus.COMPLETED

    def test_rejecting_the_offer_stops_the_workflow_without_extending_it(self) -> None:
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
        )
        tools = registry()
        engine = runtime(policy, tools)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))
        engine.advance(run)

        engine.reject(run, "offer", approver="hr.partner@example.com", reason="headcount frozen")

        assert run.state("offer").status is StepStatus.REJECTED
        offer_calls = [
            call
            for tool in [tools.resolve(ActionType.EXTEND_OFFER)]
            if isinstance(tool, RecordingTool)
            for call in tool.calls
            if call.action_type is ActionType.EXTEND_OFFER
        ]
        assert offer_calls == []


class TestOnboarding:
    def test_a_background_check_parks_the_run_awaiting_the_provider(self) -> None:
        policy = TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType))
        engine = runtime(policy)
        run = engine.start(ONBOARDING, TENANT, "employee-7", context=dict(CONTEXT))

        engine.advance(run)

        assert run.state("background_check").status is StepStatus.AWAITING_EXTERNAL
        assert run.status is RunStatus.BLOCKED
        assert run.state("provision_access").status is StepStatus.PENDING

    def test_resolving_the_external_result_resumes_the_run_days_later(self) -> None:
        policy = TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType))
        engine = runtime(policy)
        run = engine.start(ONBOARDING, TENANT, "employee-7", context=dict(CONTEXT))
        engine.advance(run)

        engine.resolve_external(run, "background_check", {"verdict": "CLEAR"})
        engine.resolve_external(run, "order_hardware", {"tracking": "KE-9931"})

        assert run.state("provision_access").status is StepStatus.COMPLETED
        assert run.state("learning_path").status is StepStatus.COMPLETED
        assert run.status is RunStatus.COMPLETED

    def test_a_failed_background_check_stops_provisioning(self) -> None:
        policy = TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType))
        engine = runtime(policy)
        run = engine.start(ONBOARDING, TENANT, "employee-7", context=dict(CONTEXT))
        engine.advance(run)

        engine.resolve_external(run, "background_check", {"verdict": "FAILED"}, succeeded=False)

        assert run.state("provision_access").status is StepStatus.PENDING
        assert run.status is RunStatus.FAILED

    def test_context_accumulates_across_steps(self) -> None:
        policy = TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType))
        engine = runtime(policy)
        run = engine.start(
            ONBOARDING, TENANT, "employee-7", context={**CONTEXT, "role": "engineer"}
        )
        engine.advance(run)
        engine.resolve_external(run, "background_check", {"verdict": "CLEAR"})

        assert run.context["role"] == "engineer"
        assert run.context["verdict"] == "CLEAR"


class TestFailureHandling:
    def test_a_tool_failure_fails_the_step_and_the_run(self) -> None:
        engine = runtime(
            TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType)),
            registry(failing=frozenset({ActionType.SCORE_CANDIDATE})),
        )
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        assert run.state("source").status is StepStatus.FAILED
        assert run.status is RunStatus.FAILED

    def test_a_missing_tool_for_a_required_step_fails_rather_than_silently_passing(self) -> None:
        engine = AgentRuntime(
            gate=GovernanceGate(
                TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType))
            ),
            tools=ToolRegistry(),
        )
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        assert run.state("source").status is StepStatus.FAILED

    def test_approving_a_step_that_is_not_awaiting_approval_is_an_error(self) -> None:
        engine = runtime()
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))
        engine.advance(run)

        with pytest.raises(ApprovalError, match="not awaiting approval"):
            engine.approve(run, "source", approver="hr.partner@example.com")

    def test_an_approval_must_name_the_approver(self) -> None:
        engine = runtime()
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))
        engine.advance(run)

        with pytest.raises(ApprovalError, match="name the approver"):
            engine.approve(run, "shortlist", approver="  ")

    def test_a_denied_action_never_reaches_a_tool(self) -> None:
        tools = registry()
        engine = runtime(TenantPolicy.conservative(str(TENANT)), tools)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        recording = tools.resolve(ActionType.EXTEND_OFFER)
        assert isinstance(recording, RecordingTool)
        assert not any(call.action_type is ActionType.EXTEND_OFFER for call in recording.calls)


class TestAuditTrail:
    def test_every_step_is_written_to_the_ledger(self) -> None:
        ledger = DecisionLedger()
        engine = runtime(ledger=ledger)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))

        engine.advance(run)

        assert len(ledger) >= 2
        assert ledger.verify().intact

    def test_a_human_approval_is_attributed_in_the_ledger(self) -> None:
        ledger = DecisionLedger()
        policy = TenantPolicy(
            tenant_id=str(TENANT),
            autonomous_actions=frozenset(ActionType) - {ActionType.EXTEND_OFFER},
        )
        engine = runtime(policy, ledger=ledger)
        run = engine.start(TALENT_ACQUISITION, TENANT, "candidate-42", context=dict(CONTEXT))
        engine.advance(run)

        engine.approve(run, "offer", approver="hr.partner@example.com")

        approvals = ledger.human_approvals()
        assert approvals
        assert approvals[-1].approver == "hr.partner@example.com"
        assert approvals[-1].action_type == str(ActionType.EXTEND_OFFER)

    def test_the_ledger_can_be_filtered_to_one_candidate(self) -> None:
        ledger = DecisionLedger()
        engine = runtime(ledger=ledger)
        engine.advance(
            engine.start(TALENT_ACQUISITION, TENANT, "candidate-1", context=dict(CONTEXT))
        )
        engine.advance(
            engine.start(TALENT_ACQUISITION, TENANT, "candidate-2", context=dict(CONTEXT))
        )

        assert ledger.for_subject("candidate-1")
        assert all(entry.subject_id == "candidate-1" for entry in ledger.for_subject("candidate-1"))
