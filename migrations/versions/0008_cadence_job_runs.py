"""cadence job-status rows (CofC write-path ruling 2026-07-08: one row per
run of the perspective-snapshot / ask-corpus-drift cadence jobs)

Revision ID: 0008_cadence_job_runs
Revises: 0007_threat_intel_refs
Create Date: 2026-07-08
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_cadence_job_runs"
down_revision = "0007_threat_intel_refs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cadence_job_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job", sa.String(length=64), nullable=False, index=True),
        sa.Column("status", sa.String(length=16), nullable=False),   # running|ok|failed
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("rows_affected", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
    )


def downgrade():
    op.drop_table("cadence_job_runs")
