"""score change intelligence: changed-only events + per-run aggregates

Captures axis-label flips at rescore-import time so change-over-time data can
feed corpus improvements and SFT refinement (chairman directive 2026-07-18).
Storage discipline for the 1GB Fly PG: UNCHANGED refreshes are aggregated in
score_change_runs, never stored per-row; score_change_events holds CHANGED
rows only (label_index flip or escalation flip).

Revision ID: 0009_score_change_events
Revises: 0008_cadence_job_runs
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_score_change_events"
down_revision = "0008_cadence_job_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "score_change_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("server_id", sa.String(128), nullable=False),
        sa.Column("axis_name", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64)),
        sa.Column("prev_label", sa.String(64)),
        sa.Column("prev_label_index", sa.Integer),
        sa.Column("prev_p_top", sa.Float),
        sa.Column("prev_scored_at", sa.DateTime),
        sa.Column("new_label", sa.String(64)),
        sa.Column("new_label_index", sa.Integer),
        sa.Column("new_p_top", sa.Float),
        sa.Column("event_ts", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_sce_server_ts", "score_change_events", ["server_id", "event_ts"])
    op.create_index("ix_sce_axis_ts", "score_change_events", ["axis_name", "event_ts"])
    op.create_index("ix_sce_run", "score_change_events", ["run_id"])
    op.create_table(
        "score_change_runs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(64)),
        sa.Column("axis_name", sa.String(64), nullable=False),
        sa.Column("n_new", sa.Integer, server_default="0"),
        sa.Column("n_changed", sa.Integer, server_default="0"),
        sa.Column("n_unchanged", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "axis_name", name="uq_scr_run_axis"),
    )


def downgrade() -> None:
    op.drop_table("score_change_runs")
    op.drop_index("ix_sce_run", table_name="score_change_events")
    op.drop_index("ix_sce_axis_ts", table_name="score_change_events")
    op.drop_index("ix_sce_server_ts", table_name="score_change_events")
    op.drop_table("score_change_events")
