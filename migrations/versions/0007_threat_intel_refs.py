"""threat-intel references (OTX pulse context layer over exact vuln links)

Revision ID: 0007_threat_intel_refs
Revises: 0006_axis_score_indexes
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_threat_intel_refs"
down_revision = "0006_axis_score_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "threat_intel_refs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("indicator_type", sa.String(length=16), nullable=False, index=True),   # cve | domain
        sa.Column("indicator_value", sa.String(length=256), nullable=False, index=True),
        sa.Column("pulse_id", sa.String(length=64), nullable=False),
        sa.Column("pulse_name", sa.String(length=512), nullable=True),
        sa.Column("pulse_created", sa.DateTime(), nullable=True),
        sa.Column("is_aggregator", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=16), nullable=False),                        # otx
        sa.Column("source_url", sa.Text(), nullable=False),                               # THE provenance anchor
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("indicator_type", "indicator_value", "pulse_id",
                            name="uq_indicator_pulse"),
    )


def downgrade():
    op.drop_table("threat_intel_refs")
