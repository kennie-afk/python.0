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
    version: int | None = None
    gate: str | None = None
    data_hash: str | None = None
    created_by: str | None = None
    findings: list[str] = Field(default_factory=list)

class ModelVersionView(BaseModel):
    version: int
    algorithm: str
    rows: int
    positives: int
    data_hash: str
    gate: str
    active: bool
    created_by: str
    created_at: str
    activated_at: str | None
    feature_importance: dict[str, float]
    fidelity: dict[str, Any]

class ApprovalRequest(BaseModel):
    # Identity comes from the authenticated principal. A client may still send its own name, but
    # it is only accepted when it matches, so it cannot be used to sign as somebody else.
    approver: str | None = Field(default=None, min_length=1, max_length=200)

class RejectionRequest(BaseModel):
    approver: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)

class RetryRequest(BaseModel):
    actor: str | None = Field(default=None, min_length=1, max_length=200)
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
    label: str = Field(
        default="Ad-hoc check",
        min_length=1,
        max_length=200,
        description="what was analysed, e.g. 'Backend engineer shortlist by age band'",
    )

class GroupImpactView(BaseModel):
    group: str
    selection_rate: float
    impact_ratio: float
    total: int
    selected: int
    adversely_impacted: bool

class AdverseImpactResponse(BaseModel):
    report_id: int | None = None
    label: str | None = None
    ledger_sequence: int | None = None
    recorded_at: str | None = None
    minimum_group_size: int | None = None
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
    # One label per employee (for example an age band), used ONLY to test the trained model for
    # adverse impact. It is never a model feature and is not stored.
    groups: list[str] | None = None
    minimum_group_size: int = Field(default=30, ge=1)

class TrainResponse(BaseModel):
    rows: int
    positives: int
    positive_rate: float
    algorithm: str
    feature_importance: dict[str, float]
    version: int
    gate: str
    active: bool
    findings: list[str] = Field(default_factory=list)
    fidelity: dict[str, Any] = Field(default_factory=dict)

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
    signed: int = 0
    unsigned: int = 0
    signatures_checked: bool = False

class LedgerHeadView(BaseModel):
    tenant_id: str
    sequence: int | None
    entry_hash: str
    entries: int
    signed: bool
    signature: str | None
    key_fingerprint: str | None
    generated_at: str

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


class OverviewView(BaseModel):
    runs: int
    awaiting_approval: int
    awaiting_external: int
    failed: int
    ledger_entries: int
    human_decisions: int
    screenings: dict[str, int]
    impact_reports: dict[str, int]
    retention_bands: dict[str, int]
    model_trained: bool

class StoredScreeningView(BaseModel):
    id: int
    subject_key: str
    requirement: str
    score: float
    recommendation: str
    rationale: str
    signals_considered: list[str]
    model: str
    prompt_fingerprint: str
    screened_at: str

class StoredScoreView(BaseModel):
    subject_key: str
    probability: float
    band: str
    needs_intervention: bool
    drivers: list[DriverView]
    scored_at: str

class ScoreHistoryView(BaseModel):
    subject_key: str
    probability: float
    band: str
    needs_intervention: bool
    model_version: int | None
    drivers: list[DriverView]
    scored_at: str

class KeyCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    roles: list[str] = Field(min_length=1)

class KeyView(BaseModel):
    key_id: str
    label: str
    roles: list[str]
    active: bool
    created_at: str
    created_by: str | None
    revoked_at: str | None
    tokens_valid_from: str | None

class IssuedKeyView(KeyView):
    api_key: str = Field(description="the secret; shown once and never again")

class AlertView(BaseModel):
    id: int
    kind: str
    message: str
    created_at: str
    delivered_at: str | None
    attempts: int
    last_error: str | None
