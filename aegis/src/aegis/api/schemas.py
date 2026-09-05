from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StartRunRequest(BaseModel):
    workflow: str = Field(description="workflow name from the catalogue")
    subject_id: str = Field(min_length=1, max_length=200)
    context: dict[str, Any] = Field(default_factory=dict)


class StepView(BaseModel):
    key: str
    status: str
    description: str
    action_type: str
    irreversible: bool
    reasons: list[str]
    approver: str | None
    attempts: int
    retryable: bool


class RunView(BaseModel):
    run_id: str
    workflow: str
    tenant_id: str
    subject_id: str
    status: str
    steps: list[StepView]
    pending_approvals: list[str]
    context: dict[str, Any]


class TokenRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    token: str
    tenant_id: str
    subject: str
    roles: list[str]


class WorkflowStepView(BaseModel):
    key: str
    action_type: str
    description: str
    requires: list[str]
    requires_context: list[str]
    irreversible: bool
    optional: bool


class WorkflowView(BaseModel):
    name: str
    steps: list[WorkflowStepView]
    required_context: list[str]


class ModelStatusView(BaseModel):
    trained: bool
    algorithm: str | None = None
    rows: int | None = None
    positives: int | None = None
    trained_at: str | None = None
    feature_importance: dict[str, float] = Field(default_factory=dict)


class ApprovalRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=200)


class RejectionRequest(BaseModel):
    approver: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class RetryRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=200)
    amendments: dict[str, Any] = Field(default_factory=dict)


class ExternalResultRequest(BaseModel):
    succeeded: bool = True
    result: dict[str, Any] = Field(default_factory=dict)


class AnonymizeRequest(BaseModel):
    record: dict[str, Any]


class AnonymizeResponse(BaseModel):
    subject_key: str
    attributes: dict[str, Any]
    dropped: list[str]
    pseudonymised: list[str]
    generalised: list[str]
    scrubbed_free_text: list[str]


class GroupOutcomeIn(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    selected: int = Field(ge=0)
    total: int = Field(gt=0)


class AdverseImpactRequest(BaseModel):
    outcomes: list[GroupOutcomeIn] = Field(min_length=2)
    minimum_group_size: int = Field(default=30, ge=1)


class GroupImpactView(BaseModel):
    group: str
    selection_rate: float
    impact_ratio: float
    total: int
    selected: int
    adversely_impacted: bool


class AdverseImpactResponse(BaseModel):
    verdict: str
    reference_group: str
    reference_rate: float
    groups: list[GroupImpactView]
    p_value: float | None
    summary: str


class EmployeeIn(BaseModel):
    subject_key: str = Field(min_length=1, max_length=200)
    tenure_years: float = Field(ge=0)
    months_since_promotion: float = Field(ge=0)
    salary: float = Field(gt=0)
    band_midpoint: float = Field(gt=0)
    peer_median_salary: float = Field(gt=0)
    manager_changes_24m: int = Field(default=0, ge=0)
    commute_minutes: float = Field(default=0.0, ge=0)
    engagement_score: float = Field(default=3.0, ge=0, le=5)
    training_hours_12m: float = Field(default=0.0, ge=0)
    overtime_hours_monthly: float = Field(default=0.0, ge=0)
    internal_applications_12m: int = Field(default=0, ge=0)


class DriverView(BaseModel):
    feature: str
    contribution: float
    direction: str


class AttritionScoreView(BaseModel):
    subject_key: str
    probability: float
    band: str
    needs_intervention: bool
    drivers: list[DriverView]


class TrainRequest(BaseModel):
    algorithm: str = Field(default="gradient_boosting")
    employees: list[EmployeeIn] = Field(min_length=40)
    left: list[bool] = Field(min_length=40)


class TrainResponse(BaseModel):
    rows: int
    positives: int
    positive_rate: float
    algorithm: str
    feature_importance: dict[str, float]


class ScoreRequest(BaseModel):
    employees: list[EmployeeIn] = Field(min_length=1)


class LedgerEntryView(BaseModel):
    sequence: int
    workflow: str
    step: str
    action_type: str
    subject_id: str
    outcome: str
    reasons: list[str]
    approver: str | None
    recorded_at: str


class IntegrityView(BaseModel):
    intact: bool
    entries_checked: int
    broken_at: int | None
    reason: str | None


class ScreenRequest(BaseModel):
    record: dict[str, Any]
    requirement: str = Field(min_length=1, max_length=500)


class ScreeningView(BaseModel):
    subject_key: str
    score: float
    recommendation: str
    rationale: str
    signals_considered: list[str]
    model: str
    prompt_fingerprint: str


class ProblemDetail(BaseModel):
    title: str
    detail: str
    status: int
    code: str
