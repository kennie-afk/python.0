from __future__ import annotations

from uuid import uuid4

import pytest

from aegis.agents import (
    MAX_STEP_ATTEMPTS,
    AgentRuntime,
    RecordingTool,
    RetryError,
    RunStatus,
    StepDefinition,
    StepStatus,
    Tool,
    ToolRegistry,
    ToolResult,
    WorkflowDefinition,
    WorkflowRun,
)
from aegis.governance import ActionType, GovernanceGate, TenantPolicy
from aegis.ledger import DecisionLedger

TENANT = uuid4()

WORKFLOW = WorkflowDefinition(
    name="scheduling",
    steps=(
        StepDefinition(
            key="notify",
            action_type=ActionType.SEND_MESSAGE,
            description="tell the candidate",
            requires_context=("recipient_email",),
        ),
        StepDefinition(
            key="book",
            action_type=ActionType.SCHEDULE_INTERVIEW,
            description="book the slot",
            requires=("notify",),
            requires_context=("starts_at",),
        ),
    ),
)


class ClashingCalendar(Tool):
    def __init__(self, taken: str) -> None:
        self._taken = taken
        self.calls: list[str] = []

    def handles(self) -> frozenset[ActionType]:
        return frozenset({ActionType.SCHEDULE_INTERVIEW})

    def execute(self, action):  # type: ignore[no-untyped-def]
        slot = str(action.payload.get("starts_at"))
        self.calls.append(slot)
        if slot == self._taken:
            return ToolResult(succeeded=False, detail=f"{slot} is already booked")
        return ToolResult(succeeded=True, output={"booked_at": slot})


CONTEXT = {"recipient_email": "candidate@example.com", "starts_at": "2099-01-01T09:00:00+00:00"}


def failed_run() -> tuple[AgentRuntime, WorkflowRun, ClashingCalendar, DecisionLedger]:
    clash = ClashingCalendar(taken=CONTEXT["starts_at"])
    tools = ToolRegistry()
    tools.register(RecordingTool(frozenset({ActionType.SEND_MESSAGE}), output={"sent": True}))
    tools.register(clash)
    ledger = DecisionLedger()
    runtime = AgentRuntime(
        gate=GovernanceGate(
            TenantPolicy(tenant_id=str(TENANT), autonomous_actions=frozenset(ActionType))
        ),
        tools=tools,
        ledger=ledger,
    )
    run = runtime.start(WORKFLOW, TENANT, "candidate-1", context=dict(CONTEXT))
    runtime.advance(run)
    assert run.status is RunStatus.FAILED
    return runtime, run, clash, ledger


class TestRetryingAFailedStep:
    def test_a_clash_fails_the_run_before_any_retry(self) -> None:
        _, run, _, _ = failed_run()

        assert run.state("book").status is StepStatus.FAILED
        assert "already booked" in run.state("book").reasons[0]

    def test_amending_the_slot_recovers_the_run(self) -> None:
        runtime, run, clash, _ = failed_run()

        runtime.retry(
            run,
            "book",
            actor="recruiter@example.com",
            amendments={"starts_at": "2099-01-02T09:00:00+00:00"},
        )

        assert run.state("book").status is StepStatus.COMPLETED
        assert run.status is RunStatus.COMPLETED
        assert clash.calls == ["2099-01-01T09:00:00+00:00", "2099-01-02T09:00:00+00:00"]

    def test_the_completed_step_before_the_failure_is_not_run_again(self) -> None:
        runtime, run, _, _ = failed_run()
        attempts_before = run.state("notify").attempts

        runtime.retry(
            run,
            "book",
            actor="recruiter@example.com",
            amendments={"starts_at": "2099-01-02T09:00:00+00:00"},
        )

        assert run.state("notify").attempts == attempts_before

    def test_the_retry_is_written_to_the_ledger_with_who_asked(self) -> None:
        runtime, run, _, ledger = failed_run()

        runtime.retry(
            run,
            "book",
            actor="recruiter@example.com",
            amendments={"starts_at": "2099-01-02T09:00:00+00:00"},
        )

        entries = [entry for entry in ledger.entries if entry.outcome == "RETRIED"]
        assert len(entries) == 1
        assert entries[0].approver == "recruiter@example.com"
        assert "starts_at" in entries[0].reasons[0]

    def test_a_retry_must_name_who_asked_for_it(self) -> None:
        runtime, run, _clash, _ = failed_run()

        with pytest.raises(RetryError):
            runtime.retry(run, "book", actor="   ")

        assert run.state("book").status is StepStatus.FAILED


class TestRetryRefusals:
    def test_a_denied_step_cannot_be_retried_into_approval(self) -> None:
        tools = ToolRegistry()
        tools.register(RecordingTool(frozenset(ActionType), output={"ok": True}))
        runtime = AgentRuntime(
            gate=GovernanceGate(
                TenantPolicy(
                    tenant_id=str(TENANT),
                    autonomous_actions=frozenset({ActionType.SEND_MESSAGE}),
                    forbidden_actions=frozenset({ActionType.SCHEDULE_INTERVIEW}),
                )
            ),
            tools=tools,
        )
        run = runtime.start(WORKFLOW, TENANT, "candidate-1", context=dict(CONTEXT))
        runtime.advance(run)
        assert run.state("book").status is StepStatus.DENIED

        with pytest.raises(RetryError, match="a decision, not a fault"):
            runtime.retry(run, "book", actor="recruiter@example.com")

    def test_a_completed_step_cannot_be_retried(self) -> None:
        runtime, run, _, _ = failed_run()

        with pytest.raises(RetryError, match="only a FAILED step"):
            runtime.retry(run, "notify", actor="recruiter@example.com")

    def test_a_retry_cannot_smuggle_in_unrelated_context(self) -> None:
        runtime, run, _, _ = failed_run()

        with pytest.raises(RetryError, match="cannot introduce"):
            runtime.retry(
                run,
                "book",
                actor="recruiter@example.com",
                amendments={"salary": 100000},
            )

    def test_a_retry_cannot_blank_out_required_context(self) -> None:
        runtime, run, _, _ = failed_run()

        with pytest.raises(RetryError, match="cannot blank out"):
            runtime.retry(run, "book", actor="recruiter@example.com", amendments={"starts_at": ""})

    def test_a_step_stops_being_retryable_after_the_attempt_limit(self) -> None:
        runtime, run, _, _ = failed_run()

        for _index in range(MAX_STEP_ATTEMPTS - 1):
            runtime.retry(
                run,
                "book",
                actor="recruiter@example.com",
                amendments={"starts_at": "2099-01-01T09:00:00+00:00"},
            )
            assert run.state("book").status is StepStatus.FAILED

        with pytest.raises(RetryError, match="change of approach"):
            runtime.retry(
                run,
                "book",
                actor="recruiter@example.com",
                amendments={"starts_at": "2099-01-03T09:00:00+00:00"},
            )
