from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

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
    text,
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
    # HMAC over the entry hash with a key held outside the database (AEGIS_LEDGER_SIGNING_KEY).
    # Null for entries written before signing was enabled.
    signature: Mapped[str | None] = mapped_column(String(64), nullable=True)

class ModelRow(Base):
    """The legacy one-pickle-per-tenant table. Kept only so old rows can be converted; nothing
    reads a payload from it any more. See ModelVersionRow."""

    __tablename__ = "attrition_models"

    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    positives: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_importance: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    trained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class ModelVersionRow(Base):
    """One trained attrition model. Rows are never overwritten: a retrain adds a version, and
    which one serves is the `active` flag. The payload is an .npz of plain numeric arrays (no
    pickle) and the signature is an HMAC over it made with a key the database does not hold."""

    __tablename__ = "attrition_model_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_model_tenant_version"),
        Index(
            "uq_model_one_active",
            "tenant_id",
            unique=True,
            postgresql_where=text("active"),
            sqlite_where=text("active"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(60), nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    positives: Mapped[int] = mapped_column(Integer, nullable=False)
    feature_importance: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    data_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False, default="npz-v1")
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    gate: Mapped[str] = mapped_column(String(10), nullable=False)
    fidelity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ApiKeyRow(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("ix_api_keys_tenant", "tenant_id"),
        Index("uq_api_keys_key_id", "key_id", unique=True),
    )

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A surrogate id that tokens carry instead of anything derived from the key itself.
    key_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    # Tokens issued from this key before this moment are refused, without revoking the key.
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)

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


class RiskScoreHistoryRow(Base):
    """Append-only: every score ever given, with the model version that gave it."""

    __tablename__ = "attrition_score_history"
    __table_args__ = (
        Index("ix_score_history_subject", "tenant_id", "subject_key", "scored_at"),
        Index("ix_score_history_tenant_time", "tenant_id", "scored_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(200), nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    needs_intervention: Mapped[bool] = mapped_column(Boolean, default=False)
    drivers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # The engineered feature values the model saw, kept so live drift can be measured later.
    features: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class VerificationReportRow(Base):
    """A stored verification result (determinism, drift or fidelity)."""

    __tablename__ = "verification_reports"
    __table_args__ = (Index("ix_verification_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    verdict: Mapped[str] = mapped_column(String(40), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ledger_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class AlertRow(Base):
    """An alert waiting for, or already given, delivery. Written first, delivered after, so a
    mail server or webhook that is down delays an alert rather than losing it."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

class CalendarSlotRow(Base):
    """An interview slot that is booked. Per tenant: two tenants may share an attendee name."""

    __tablename__ = "calendar_slots"
    __table_args__ = (Index("ix_calendar_attendee", "tenant_id", "attendee", "starts_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    attendee: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    event_ref: Mapped[str] = mapped_column(String(100), nullable=False)
