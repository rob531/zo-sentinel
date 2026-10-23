"""index mcp_llm_axis_scores.scored_at for the freshness recency aggregates (FU-667)

The public /freshness surface (scoring_freshness_surface.py) computes
MAX(scored_at) and MIN(scored_at) over the whole mcp_llm_axis_scores table to
publish scoring recency. scored_at carried NO index, so each was a full seq
scan over the multi-hundred-k (and growing, append-only) score table. Combined
with the COUNT/COUNT(DISTINCT)/anti-join in the same handler, a cold /freshness
exceeded 45s on the small Fly Postgres and the route hung (HTTP 000) -- the
FU-667 read-surface outage. A btree on scored_at turns MIN/MAX into an
index-only lookup (fetch one endpoint of the btree) instead of a scan.

Ops note (council ruling 2026-07-03, see 0006_axis_score_indexes): plain CREATE
INDEX takes a lock and CONCURRENTLY cannot run inside alembic's transaction,
and a hung migration in Fly's release_command blocks the whole deploy. So on
PROD this index is PRE-CREATED manually via `fly proxy` with
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_axis_scores_scored_at
    ON mcp_llm_axis_scores (scored_at);
BEFORE the deploy, so release_command's `CREATE INDEX IF NOT EXISTS` below is a
no-op (the index already exists) and never takes a blocking lock. Fresh/dev/CI
databases (small) get it created here normally, in-transaction, which is fine
at their size. Because the release role ($OWNER_DATABASE_URL) OWNS this table,
no ownership-guard SELECT is needed -- the statement is a plain additive
`op.execute`, which the migration content-class grader reads as GREEN
(EXPAND-ONLY / additive). The statement_timeout guard in read_surface_guard.py
is the belt to this index's braces: even before the index is present the route
fails fast (503) instead of hanging.
"""
from alembic import op

revision = "0014_axis_scored_at_index"
down_revision = "0013_registry_stable_repo_id"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_axis_scores_scored_at"
_INDEX_SPEC = "mcp_llm_axis_scores (scored_at)"


def upgrade():
    # Additive, idempotent: CREATE INDEX IF NOT EXISTS. On prod the index is
    # pre-created CONCURRENTLY (ops note above) so this is a no-op; on dev/CI
    # (small, owned) it creates the index in-transaction.
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} ON {_INDEX_SPEC}")


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
