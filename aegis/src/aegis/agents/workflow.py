from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from aegis.governance.actions import ActionType


class StepStatus(StrEnum):
    PENDING = "PENDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AWAITING_EXTERNAL = "AWAITING_EXTERNAL"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    DENIED = "DENIED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_STEP_STATUSES: frozenset[StepStatus] = frozenset(
    {
        StepStatus.COMPLETED,
        StepStatus.REJECTED,
        StepStatus.FAILED,
        StepStatus.SKIPPED,
        StepStatus.DENIED,
    }
)


@dataclass(frozen=True, slots=True)
class StepDefinition:
    key: str
    action_type: ActionType
    description: str
    requires: tuple[str, ...] = ()
    awaits_external: bool = False
    optional: bool = False


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    name: str
    steps: tuple[StepDefinition, ...]

    def __post_init__(self) -> None:
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError(f"workflow {self.name} has duplicate step keys")

        known: set[str] = set()
        for step in self.steps:
            missing = set(step.requires) - known
            if missing:
                raise ValueError(
                    f"step {step.key!r} depends on {sorted(missing)} which is not defined before it"
                )
            known.add(step.key)

    def step(self, key: str) -> StepDefinition:
        for step in self.steps:
            if step.key == key:
                return step
        raise KeyError(f"workflow {self.name} has no step {key!r}")


@dataclass(slots=True)
class StepState:
    key: str
    status: StepStatus = StepStatus.PENDING
    reasons: tuple[str, ...] = ()
    result: dict[str, Any] = field(default_factory=dict)
    approver: str | None = None
    attempts: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STEP_STATUSES

    def transition(self, status: StepStatus, reasons: Sequence[str] = ()) -> None:
        self.status = status
        self.reasons = tuple(reasons)
        self.updated_at = datetime.now(UTC)


@dataclass(slots=True)
class WorkflowRun:
    definition: WorkflowDefinition
    tenant_id: UUID
    subject_id: str
    context: dict[str, Any] = field(default_factory=dict)
    steps: dict[str, StepState] = field(default_factory=dict)
    run_id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.steps:
            self.steps = {step.key: StepState(key=step.key) for step in self.definition.steps}

    @property
    def status(self) -> RunStatus:
        states = [self.steps[step.key] for step in self.definition.steps]

        if any(state.status is StepStatus.FAILED for state in states):
            return RunStatus.FAILED
        if all(state.is_terminal for state in states):
            return RunStatus.COMPLETED
        if any(
            state.status in (StepStatus.AWAITING_APPROVAL, StepStatus.AWAITING_EXTERNAL)
            for state in states
        ):
            return RunStatus.BLOCKED
        return RunStatus.RUNNING

    @property
    def is_finished(self) -> bool:
        return self.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

    def state(self, key: str) -> StepState:
        if key not in self.steps:
            raise KeyError(f"run has no step {key!r}")
        return self.steps[key]

    def runnable_steps(self) -> tuple[StepDefinition, ...]:
        ready: list[StepDefinition] = []
        for step in self.definition.steps:
            if self.steps[step.key].status is not StepStatus.PENDING:
                continue
            if all(self._dependency_satisfied(key) for key in step.requires):
                ready.append(step)
        return tuple(ready)

    def blocked_steps(self) -> tuple[StepState, ...]:
        return tuple(
            state
            for state in self.steps.values()
            if state.status in (StepStatus.AWAITING_APPROVAL, StepStatus.AWAITING_EXTERNAL)
        )

    def _dependency_satisfied(self, key: str) -> bool:
        state = self.steps[key]
        if state.status is StepStatus.COMPLETED:
            return True
        return state.status is StepStatus.SKIPPED and self.definition.step(key).optional
