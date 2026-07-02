"""perspectives + ask corpus tables (v1.1 Perspectives / v2 Ask slice)

Revision ID: 0004_perspectives_ask
Revises: 0003_score_disputes
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_perspectives_ask"
down_revision = "0003_score_disputes"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "perspectives",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("org_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("facet_filters", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "perspective_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("perspective_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("taken_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("membership", sa.JSON(), nullable=True),
    )
    op.create_table(
        "perspective_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("perspective_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("server_id", sa.String(length=128), nullable=False, index=True),
        sa.Column("change_type", sa.String(length=16), nullable=False),
        sa.Column("old_tier", sa.String(length=32), nullable=True),
        sa.Column("new_tier", sa.String(length=32), nullable=True),
        sa.Column("seen", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "ask_corpus_index",
        sa.Column("server_id", sa.String(length=128), primary_key=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("terms", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=32), nullable=True),
        sa.Column("indexed_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_table("ask_corpus_index")
    op.drop_table("perspective_events")
    op.drop_table("perspective_snapshots")
    op.drop_table("perspectives")
