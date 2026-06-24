"""initial app schema: orgs, users, api_keys

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(255), index=True),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("org_id", sa.String(64), sa.ForeignKey("orgs.id"), index=True),
        sa.Column("role", sa.String(32)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.String(64), sa.ForeignKey("orgs.id"), index=True),
        sa.Column("key_hash", sa.String(255)),
        sa.Column("label", sa.String(128)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
    op.drop_table("users")
    op.drop_table("orgs")
