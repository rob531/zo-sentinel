"""materialize canonical_family on mcp_server_registry

Restores the promise of the Commit-B canonicalizer (Feb-Apr 2026, DuckDB mesh
era): every registry row gets a stable project-family identity so github twin
+ npm listing + pypi listing aggregate together. The ecosyste.ms cousin KV
that powered rules 2-4 of the original did not survive the mesh rebuild, so
this migration ports the surviving doctrine (sticky assignment, deterministic
rules only, provenance stamped) with the deterministic subset of rules; an
ecosystems re-enrichment lane can upgrade rows later under the same contract.

Revision ID: 0010_canonical_family
Revises: 0009_score_change_events
Create Date: 2026-07-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_canonical_family"
down_revision = "0009_score_change_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_server_registry", sa.Column("canonical_family", sa.String(512)))
    op.add_column("mcp_server_registry", sa.Column("canonical_rule", sa.String(32)))
    op.add_column("mcp_server_registry", sa.Column("canonical_set_at", sa.DateTime))
    op.create_index("ix_registry_canonical_family", "mcp_server_registry", ["canonical_family"])


def downgrade() -> None:
    op.drop_index("ix_registry_canonical_family", table_name="mcp_server_registry")
    op.drop_column("mcp_server_registry", "canonical_set_at")
    op.drop_column("mcp_server_registry", "canonical_rule")
    op.drop_column("mcp_server_registry", "canonical_family")
