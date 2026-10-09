"""create the canonical mcp_discovery_candidates (FU-596)

The pre-registry discovery funnel was SEVERED: four ingestors defined
`mcp_discovery_candidates` with three incompatible shapes and there was NO
migration, so the table was never created on the live bus.  Discovery writes
earned a 2xx enqueue receipt and vanished (`Catalog Error: table does not
exist`); ~8.5K candidates/day spooled to bus_write_guard (34K rows by
2026-10-09, 0 lost) and net registry intake collapsed to ~10/day against a floor
of 100, starving scoring coverage.

This migration ends the DDL war by creating ONE canonical table that:
  * matches every promoter's SELECT (id, candidate_name, candidate_url,
    candidate_description, discovered_in_directory, discovered_status, promoted);
  * accepts the 34K spooled github/npm rows verbatim (directory vocabulary,
    no id supplied -> `id` is auto-assigned);
  * keeps the reference/registry ingestors' `ON CONFLICT (discovered_in_directory,
    candidate_name)` upserts working;
  * preserves the pypi paginators' rich package fields in `discovery_metadata`.

The canonical shape is defined once in `discovery_candidates_schema.py`; this
migration mirrors it and a test asserts the two agree.

Revision ID: 0014_discovery_candidates_canonical
Revises: 0013_registry_stable_repo_id
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_discovery_candidates_canonical"
down_revision = "0013_registry_stable_repo_id"
branch_labels = None
depends_on = None

# Mirrors discovery_candidates_schema.CANDIDATE_COLUMNS (asserted by test).
_COLUMNS = [
    "id",
    "candidate_name",
    "candidate_url",
    "candidate_description",
    "discovered_in_directory",
    "discovered_status",
    "promoted",
    "first_seen",
    "last_seen",
    "reviewed_at",
    "discovery_metadata",
]


def upgrade() -> None:
    op.create_table(
        "mcp_discovery_candidates",
        # BigInteger PK, autoincrement so a row that omits `id` (github/npm/
        # directory/pypi dict writes) is assigned one, while a row that supplies
        # a deterministic md5-derived id (reference/registry) is honoured.
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("candidate_name", sa.String(512), nullable=False),
        sa.Column("candidate_url", sa.String(1024)),
        sa.Column("candidate_description", sa.Text),
        sa.Column("discovered_in_directory", sa.String(128), nullable=False),
        sa.Column("discovered_status", sa.String(32), server_default="active"),
        sa.Column("promoted", sa.Boolean, server_default=sa.false()),
        sa.Column("first_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("discovery_metadata", sa.Text),
        # The content key: makes a spool drain idempotent / 0-loss and is the
        # ON CONFLICT target the reference/registry ingestors already name.
        sa.UniqueConstraint(
            "discovered_in_directory", "candidate_name",
            name="uq_candidate_directory_name",
        ),
    )
    # Supports the promoters' hot query: WHERE discovered_in_directory=? AND
    # (promoted IS FALSE OR promoted IS NULL).
    op.create_index(
        "ix_candidates_directory_promoted",
        "mcp_discovery_candidates",
        ["discovered_in_directory", "promoted"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidates_directory_promoted", table_name="mcp_discovery_candidates")
    op.drop_table("mcp_discovery_candidates")
