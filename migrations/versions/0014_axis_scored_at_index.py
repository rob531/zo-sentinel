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
PROD this index is pre-created manually via `fly proxy` with
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_axis_scores_scored_at
    ON mcp_llm_axis_scores (scored_at);
and this migration's existence check is then a no-op there. Fresh/dev/CI
databases (small) get it created here normally. The statement_timeout guard in
read_surface_guard.py is the belt to this index's braces: even if the index is
not yet present in prod, the route now fails fast (503) instead of hanging.
"""
from alembic import op

revision = "0014_axis_scored_at_index"
down_revision = "0013_registry_stable_repo_id"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_axis_scores_scored_at"
_INDEX_SPEC = "mcp_llm_axis_scores (scored_at)"


def upgrade():
    # Postgres checks table OWNERSHIP before the IF NOT EXISTS short-circuit
    # (RangeVarCallbackOwnsRelation runs at table lookup), so the app-role
    # migration user fails on CREATE INDEX IF NOT EXISTS even when the index
    # already exists. Guard with an explicit pg_indexes existence check; sqlite
    # (CI/dev) keeps the plain IF NOT EXISTS path and owns everything anyway.
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s", (_INDEX_NAME,)
        ).first()
        if exists:
            return
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX_NAME} ON {_INDEX_SPEC}")


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
