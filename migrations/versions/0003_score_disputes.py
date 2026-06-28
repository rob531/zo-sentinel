"""score disputes table

Revision ID: 0003_score_disputes
Revises: 0002_registry_scores
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_score_disputes"
down_revision = "0002_registry_scores"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "mcp_score_disputes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("server_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("submitted_by", sa.String(length=128), nullable=False, index=True),
        sa.Column("proposed_overall_risk", sa.String(length=16), nullable=False),
        sa.Column("proposed_axes", sa.JSON(), nullable=True),
        sa.Column("reason_category", sa.String(length=48), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_mcp_score_disputes_server_id", "mcp_score_disputes", ["server_id"])
    op.create_index("ix_mcp_score_disputes_submitted_by", "mcp_score_disputes", ["submitted_by"])


def downgrade():
    op.drop_table("mcp_score_disputes")
