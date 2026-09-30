from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)

class Base(DeclarativeBase):
    pass

class TenantRow(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    autonomous_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    forbidden_actions: Mapped[list[str]] = mapped_column(JSON, default=list)
    readable_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence_floor: Mapped[float] = mapped_column(Float, default=0.70)
    approver_role: Mapped[str] = mapped_column(String(100), default="HR_BUSINESS_PARTNER")
    escalation_role: Mapped[str] = mapped_column(String(100), default="HR_BUSINESS_PARTNER")

class RunRow(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_tenant", "tenant_id"),
        Index("ix_workflow_runs_subject", "tenant_id", "subject_id"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    steps: Mapped[list[StepRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )

class StepRow(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("run_id", "step_key", name="uq_run_step"),
        Index("ix_workflow_steps_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflow_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    run: Mapped[RunRow] = relationship(back_populates="steps")

class LedgerRow(Base):
    __tablename__ = "decision_ledger"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sequence", name="uq_ledger_tenant_sequence"),
        Index("ix_ledger_subject", "tenant_id", "subject_id"),
        Index("ix_ledger_tenant_outcome", "tenant_id", "outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    workflow: Mapped[str] = mapped_column(String(100), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    step: Mapped[str] = mapped_column(String(100), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent: Mapped[str] = mapped_column(String(100), nullable=False)
    outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    approver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)

class ModelRow(Base):
    __tablename__ = "attrition_models"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    positives: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_importance: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class ApiKeyRow(Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_tenant", "tenant_id"),)

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ScreeningRow(Base):
    """One screening verdict. Holds only the pseudonymous subject key, never the record."""

    __tablename__ = "screenings"
    __table_args__ = (
        Index("ix_screenings_tenant_created", "tenant_id", "created_at"),
        Index("ix_screenings_subject", "tenant_id", "subject_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    requirement: Mapped[str] = mapped_column(String(500), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class ImpactReportRow(Base):
    """A stored adverse impact analysis, so findings outlive the request that produced them."""

    __tablename__ = "impact_reports"
    __table_args__ = (Index("ix_impact_reports_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    verdict: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_group: Mapped[str] = mapped_column(String(100), nullable=False)
    reference_rate: Mapped[float] = mapped_column(Float, nullable=False)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum_group_size: Mapped[int] = mapped_column(Integer, nullable=False)
    groups: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    ledger_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class RiskScoreRow(Base):
    """The latest retention score for each employee, so the retention view has a roster."""

    __tablename__ = "attrition_scores"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject_key", name="uq_attrition_score_subject"),
        Index("ix_attrition_scores_band", "tenant_id", "band", "probability"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    needs_intervention: Mapped[bool] = mapped_column(Boolean, default=False)
    drivers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
