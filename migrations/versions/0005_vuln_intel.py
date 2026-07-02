"""vuln-intel tables (advisories + deterministic registry links)

Revision ID: 0005_vuln_intel
Revises: 0004_perspectives_ask
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_vuln_intel"
down_revision = "0004_perspectives_ask"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "vuln_advisories",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("feed", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=True),
        sa.Column("ecosystem", sa.String(length=32), nullable=True, index=True),
        sa.Column("package", sa.String(length=256), nullable=True, index=True),
        sa.Column("affected_ranges", sa.JSON(), nullable=True),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("identities", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=32), nullable=True),
    )
    op.create_table(
        "vuln_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("advisory_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("server_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("match_basis", sa.String(length=32), nullable=False),
        sa.Column("match_value", sa.String(length=256), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=False),
        sa.Column("linked_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("advisory_id", "server_id", name="uq_advisory_server"),
    )


def downgrade():
    op.drop_table("vuln_links")
    op.drop_table("vuln_advisories")
