"""screenings, impact reports and retention scores

Revision ID: c3a9d41e7f20
Revises: bec01fc2ff72
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c3a9d41e7f20'
down_revision: str | None = 'bec01fc2ff72'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table('screenings',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('subject_key', sa.String(length=200), nullable=False),
    sa.Column('requirement', sa.String(length=500), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('recommendation', sa.String(length=20), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('signals', sa.JSON(), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('prompt_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_screenings_tenant_created', 'screenings', ['tenant_id', 'created_at'], unique=False)
    op.create_index('ix_screenings_subject', 'screenings', ['tenant_id', 'subject_key'], unique=False)
    op.create_table('impact_reports',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('verdict', sa.String(length=40), nullable=False),
    sa.Column('reference_group', sa.String(length=100), nullable=False),
    sa.Column('reference_rate', sa.Float(), nullable=False),
    sa.Column('p_value', sa.Float(), nullable=True),
    sa.Column('minimum_group_size', sa.Integer(), nullable=False),
    sa.Column('groups', sa.JSON(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('ledger_sequence', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_impact_reports_tenant_created', 'impact_reports', ['tenant_id', 'created_at'], unique=False)
    op.create_table('attrition_scores',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('subject_key', sa.String(length=200), nullable=False),
    sa.Column('probability', sa.Float(), nullable=False),
    sa.Column('band', sa.String(length=20), nullable=False),
    sa.Column('needs_intervention', sa.Boolean(), nullable=False),
    sa.Column('drivers', sa.JSON(), nullable=False),
    sa.Column('scored_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'subject_key', name='uq_attrition_score_subject')
    )
    op.create_index('ix_attrition_scores_band', 'attrition_scores', ['tenant_id', 'band', 'probability'], unique=False)
    # The audit trail and the run list are read newest-first and filtered; without these the
    # console degrades to a full scan once a tenant has tens of thousands of entries.
    op.create_index('ix_ledger_tenant_outcome', 'decision_ledger', ['tenant_id', 'outcome'], unique=False)
    op.create_index('ix_workflow_steps_status', 'workflow_steps', ['status'], unique=False)

def downgrade() -> None:
    op.drop_index('ix_workflow_steps_status', table_name='workflow_steps')
    op.drop_index('ix_ledger_tenant_outcome', table_name='decision_ledger')
    op.drop_index('ix_attrition_scores_band', table_name='attrition_scores')
    op.drop_table('attrition_scores')
    op.drop_index('ix_impact_reports_tenant_created', table_name='impact_reports')
    op.drop_table('impact_reports')
    op.drop_index('ix_screenings_subject', table_name='screenings')
    op.drop_index('ix_screenings_tenant_created', table_name='screenings')
    op.drop_table('screenings')
