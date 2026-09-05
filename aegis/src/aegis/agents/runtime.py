from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from aegis.agents.tools import ToolRegistry, ToolResult
from aegis.agents.workflow import (
    RunStatus,
    StepDefinition,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
)
from aegis.governance.actions import ProposedAction
from aegis.governance.gate import GovernanceGate
from aegis.governance.policy import Verdict
from aegis.ledger.record import DecisionLedger

RationaleBuilder = Callable[[StepDefinition, WorkflowRun], str]
ConfidenceSource = Callable[[StepDefinition, WorkflowRun], float | None]


class ApprovalError(RuntimeError):
    pass


class MissingContextError(ValueError):
    pass


class RetryError(RuntimeError):
    pass


MAX_STEP_ATTEMPTS = 3


def _default_rationale(step: StepDefinition, run: WorkflowRun) -> str:
    return f"{step.description} for {run.subject_id} in workflow {run.definition.name}"


class AgentRuntime:
    def __init__(
        self,
        gate: GovernanceGate,
        tools: ToolRegistry,
        ledger: DecisionLedger | None = None,
        agent_name: str = "aegis-runtime",
        rationale: RationaleBuilder = _default_rationale,
        confidence: ConfidenceSource | None = None,
    ) -> None:
        self._gate = gate
        self._tools = tools
        self._ledger = ledger
        self._agent_name = agent_name
        self._rationale = rationale
        self._confidence = confidence

    def start(
        self,
        definition: WorkflowDefinition,
        tenant_id: Any,
        subject_id: str,
        context: Mapping[str, Any] | None = None,
    ) -> WorkflowRun:
        supplied = dict(context or {})
        missing = definition.missing_context(supplied)
        if missing:
            raise MissingContextError(
                f"workflow {definition.name!r} cannot start without "
                + ", ".join(missing)
                + "; these are required by its steps and supplying them later would mean "
                "failing partway through a run that has already acted"
            )

        return WorkflowRun(
            definition=definition,
            tenant_id=tenant_id,
            subject_id=subject_id,
            context=supplied,
        )

    def advance(self, run: WorkflowRun, max_steps: int = 50) -> WorkflowRun:
        executed = 0
        while executed < max_steps:
            ready = run.runnable_steps()
            if not ready:
                break
            for step in ready:
                self._execute(run, step)
                executed += 1
            if run.status is RunStatus.FAILED:
                break
        return run

    def approve(self, run: WorkflowRun, step_key: str, approver: str) -> WorkflowRun:
        state = run.state(step_key)
        if state.status is not StepStatus.AWAITING_APPROVAL:
            raise ApprovalError(f"step {step_key!r} is {state.status} and is not awaiting approval")
        if not approver.strip():
            raise ApprovalError("an approval must name the approver")

        state.approver = approver
        step = run.definition.step(step_key)
        self._invoke(run, step, approved_by=approver)
        return self.advance(run)

    def reject(self, run: WorkflowRun, step_key: str, approver: str, reason: str) -> WorkflowRun:
        state = run.state(step_key)
        if state.status is not StepStatus.AWAITING_APPROVAL:
            raise ApprovalError(f"step {step_key!r} is {state.status} and is not awaiting approval")
        state.approver = approver
        state.transition(StepStatus.REJECTED, (f"rejected by {approver}: {reason}",))
        self._record(run, step_key, "REJECTED", (f"rejected by {approver}: {reason}",), approver)
        return run

    def resolve_external(
        self, run: WorkflowRun, step_key: str, result: Mapping[str, Any], succeeded: bool = True
    ) -> WorkflowRun:
        state = run.state(step_key)
        if state.status is not StepStatus.AWAITING_EXTERNAL:
            raise ApprovalError(
                f"step {step_key!r} is {state.status} and is not awaiting an external result"
            )

        state.result = dict(result)
        run.context.update(state.result)
        if succeeded:
            state.transition(StepStatus.COMPLETED, ("external result received",))
        else:
            state.transition(StepStatus.FAILED, ("external result reported failure",))
        self._record(run, step_key, state.status, state.reasons, None)
        return self.advance(run)

    def retry(
        self,
        run: WorkflowRun,
        step_key: str,
        actor: str,
        amendments: Mapping[str, Any] | None = None,
    ) -> WorkflowRun:
        state = run.state(step_key)
        if state.status is not StepStatus.FAILED:
            raise RetryError(
                f"step {step_key!r} is {state.status} and only a FAILED step may be retried; "
                "a denied or rejected step is a decision, not a fault"
            )
        if not actor.strip():
            raise RetryError("a retry must name the person requesting it")
        if state.attempts >= MAX_STEP_ATTEMPTS:
            raise RetryError(
                f"step {step_key!r} has already been attempted {state.attempts} times; "
                "it needs a change of approach rather than another attempt"
            )

        changes = dict(amendments or {})
        step = run.definition.step(step_key)
        if step.requires_context:
            unknown = set(changes) - set(step.requires_context)
            if unknown:
                raise RetryError(
                    f"step {step_key!r} takes {sorted(step.requires_context)}; "
                    f"a retry cannot introduce {sorted(unknown)}"
                )
        elif changes:
            raise RetryError(f"step {step_key!r} declares no context to amend")

        blank = [key for key, value in changes.items() if value in (None, "")]
        if blank:
            raise RetryError(f"a retry cannot blank out {sorted(blank)}")

        run.context.update(changes)
        state.result = {}
        state.approver = None
        state.transition(StepStatus.PENDING, ())
        reasons = (
            f"retried by {actor}"
            + (f" amending {', '.join(sorted(changes))}" if changes else " with no amendment"),
        )
        self._record(run, step_key, "RETRIED", reasons, actor)
        return self.advance(run)

    def pending_approvals(self, run: WorkflowRun) -> tuple[str, ...]:
        return tuple(
            state.key
            for state in run.blocked_steps()
            if state.status is StepStatus.AWAITING_APPROVAL
        )

    def _execute(self, run: WorkflowRun, step: StepDefinition) -> None:
        action = self._propose(run, step)
        decision = self._gate.evaluate(action)

        if decision.verdict is Verdict.DENY:
            run.state(step.key).transition(StepStatus.DENIED, decision.reasons)
            self._record(run, step.key, StepStatus.DENIED, decision.reasons, None)
            return

        if decision.verdict in (Verdict.REQUIRE_HUMAN_APPROVAL, Verdict.ESCALATE):
            run.state(step.key).transition(StepStatus.AWAITING_APPROVAL, decision.reasons)
            self._record(run, step.key, StepStatus.AWAITING_APPROVAL, decision.reasons, None)
            return

        self._invoke(run, step, approved_by=None)

    def _invoke(self, run: WorkflowRun, step: StepDefinition, approved_by: str | None) -> None:
        state = run.state(step.key)
        state.attempts += 1

        tool = self._tools.resolve(step.action_type)
        if tool is None:
            if step.optional:
                state.transition(StepStatus.SKIPPED, ("no tool registered for an optional step",))
                self._record(run, step.key, StepStatus.SKIPPED, state.reasons, approved_by)
                return
            state.transition(StepStatus.FAILED, (f"no tool registered for {step.action_type}",))
            self._record(run, step.key, StepStatus.FAILED, state.reasons, approved_by)
            return

        result: ToolResult = tool.execute(self._propose(run, step))
        state.result = dict(result.output)

        if not result.succeeded:
            state.transition(StepStatus.FAILED, (result.detail or "tool reported failure",))
        elif step.awaits_external:
            state.transition(StepStatus.AWAITING_EXTERNAL, ("waiting on an external system",))
        else:
            state.transition(StepStatus.COMPLETED, ())

        run.context.update(state.result)
        self._record(run, step.key, state.status, state.reasons, approved_by)

    def _propose(self, run: WorkflowRun, step: StepDefinition) -> ProposedAction:
        return ProposedAction(
            action_type=step.action_type,
            subject_id=run.subject_id,
            tenant_id=run.tenant_id,
            agent=self._agent_name,
            rationale=self._rationale(step, run),
            payload=dict(run.context),
            confidence=self._confidence(step, run) if self._confidence else None,
        )

    def _record(
        self,
        run: WorkflowRun,
        step_key: str,
        outcome: str,
        reasons: tuple[str, ...],
        approver: str | None,
    ) -> None:
        if self._ledger is None:
            return
        self._ledger.append(
            tenant_id=str(run.tenant_id),
            workflow=run.definition.name,
            run_id=str(run.run_id),
            step=step_key,
            action_type=str(run.definition.step(step_key).action_type),
            subject_id=run.subject_id,
            agent=self._agent_name,
            outcome=str(outcome),
            reasons=reasons,
            approver=approver,
        )
