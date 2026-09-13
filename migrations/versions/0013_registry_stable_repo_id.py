"""add the stable GitHub repo id to mcp_server_registry (NULLABLE, NOT unique)

The registry's identity is md5("github|" + full_name). Repo names are mutable
and, once freed, re-claimable by anyone. Measured 2026-09-10 over 38 harvest
jsonls (408,410 github rows / 354,306 distinct full_name):

  * 368 distinct full_name values carry MORE THAN ONE immutable created_at.
    Live confirmation of a seeded n=40 subset: 34 name-changed-hands,
    6 name-now-dead, 0 original-still-owns, 0 unresolved. 40/40, no false
    positives.
  * Seeded random n=400: 0.75% renamed, 1.25% gone (404). Negative control
    20/20 mangled names -> 404, PASS.
  * 2 of 3 observed renames were OWNER TRANSFERS, so no owner-derived key
    survives either.
  * html_url is NOT an alternative: 354,306 distinct urls for 354,306 distinct
    names with ZERO collisions between them -- it is literally
    "https://github.com/" + full_name.

Consequence under ON CONFLICT (server_id) DO NOTHING: when a freed name is
re-claimed, the newcomer is SILENTLY DISCARDED and the surviving row keeps its
trust_score, verdict and risk_tier while describing a repo that is gone. An
impostor inherits the original's assessment.

THIS MIGRATION DELIBERATELY DOES NOT FIX THAT. It only creates somewhere to
put the truth. The column is NULLABLE and the index is NON-UNIQUE, so insert
behaviour is bit-for-bit unchanged on a 509,336-row live table.

A UNIQUE index is intentionally deferred to a later migration, for two reasons:
  1. Every existing row is NULL until backfilled. A unique index would have to
     be PARTIAL (WHERE gh_repo_id IS NOT NULL) or ~509k NULLs collide.
  2. server_id and gh_repo_id are two conflict targets. A single INSERT names
     exactly ONE ON CONFLICT target, and a row colliding on both RAISES at
     runtime rather than skipping -- which would fail the nightly import
     closed. The importer must first resolve by gh_repo_id (UPDATE a rename in
     place), then insert the remainder ON CONFLICT (server_id) DO NOTHING.
     That restructure ships only once the backfill has told us the real number.

Backfill note: the id is already present in the GitHub SEARCH response, so
forward capture costs zero extra quota. Backfilling the existing rows is a
`harvest_refresh.py --full` re-sweep, not 354k per-repo GETs (~71h at 5k/hr).

See harvest/recon/FINDINGS.md and harvest/recon/recon_report.json.

Revision ID: 0013_registry_stable_repo_id
Revises: 0012_declare_api_usage_app_stats
Create Date: 2026-09-10
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_registry_stable_repo_id"
down_revision = "0012_declare_api_usage_app_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_server_registry", sa.Column("gh_repo_id", sa.BigInteger))
    op.add_column("mcp_server_registry", sa.Column("gh_node_id", sa.String(64)))
    op.add_column("mcp_server_registry", sa.Column("gh_id_set_at", sa.DateTime))
    # NON-UNIQUE on purpose -- a lookup aid for the reconciliation pass, not a
    # constraint. It rejects nothing and cannot fail an import closed.
    op.create_index("ix_registry_gh_repo_id", "mcp_server_registry", ["gh_repo_id"])


def downgrade() -> None:
    op.drop_index("ix_registry_gh_repo_id", table_name="mcp_server_registry")
    op.drop_column("mcp_server_registry", "gh_id_set_at")
    op.drop_column("mcp_server_registry", "gh_node_id")
    op.drop_column("mcp_server_registry", "gh_repo_id")
