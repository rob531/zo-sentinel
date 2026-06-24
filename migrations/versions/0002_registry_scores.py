"""registry + scores tables (threat-intel app data)

Revision ID: 0002_registry_scores
Revises: 0001_initial
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_registry_scores"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_server_registry",
        sa.Column("server_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(512)),
        sa.Column("registry_source", sa.String(64), index=True),
        sa.Column("url", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("trust_score", sa.Float),
        sa.Column("verdict", sa.String(64)),
        sa.Column("verdict_reasoning", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("last_assessed", sa.DateTime(timezone=True)),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("last_scanned", sa.DateTime(timezone=True)),
        sa.Column("scan_count", sa.Integer, server_default="0"),
        sa.Column("risk_tier", sa.String(32)),
        sa.Column("metadata", sa.Text),
    )
    op.create_table(
        "mcp_llm_axis_scores",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("server_id", sa.String(128), index=True, nullable=False),
        sa.Column("axis_name", sa.String(64), nullable=False),
        sa.Column("label", sa.String(64)),
        sa.Column("label_index", sa.Integer),
        sa.Column("probs", sa.JSON),
        sa.Column("p_top", sa.Float),
        sa.Column("p_critical", sa.Float),
        sa.Column("p_danger", sa.Float),
        sa.Column("escalated", sa.Boolean),
        sa.Column("escalated_to", sa.String(32)),
        sa.Column("decision_rule_version", sa.String(32)),
        sa.Column("model_version", sa.String(64), index=True, nullable=False),
        sa.Column("adapter_sha256", sa.String(80)),
        sa.Column("scored_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("server_id", "axis_name", "model_version", name="uq_axis_scores_natural"),
    )


def downgrade() -> None:
    op.drop_table("mcp_llm_axis_scores")
    op.drop_table("mcp_server_registry")