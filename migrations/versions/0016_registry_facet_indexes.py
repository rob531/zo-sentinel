"""indexes for the dashboard-summary + facet GROUP BYs on mcp_server_registry

ROOT CAUSE (measured live 2026-11-11)
-------------------------------------
Two authenticated read surfaces over the ~596k-row `mcp_server_registry` buckled
under light concurrent load (exhausted the small Fly PG pool -> HTTP 500s):

  GET /api/dashboard/summary (dashboard_summary_api.compute_summary) ~74s cold:
      SELECT risk_tier, count(*) ... WHERE risk_tier IS NOT NULL GROUP BY risk_tier
      SELECT registry_source, count(*) ... GROUP BY registry_source  (already indexed)
      plus COUNT(*) and COUNT(*) WHERE risk_tier IS NOT NULL.

  GET /api/facets (facet_enum_service.compute_facets) slow cold:
      SELECT risk_tier,        count(*) ... GROUP BY risk_tier
      SELECT verdict,          count(*) ... GROUP BY verdict
      SELECT registry_source,  count(*) ... GROUP BY registry_source  (already indexed)
      SELECT min(trust_score), max(trust_score) ...

`risk_tier`, `verdict` and `trust_score` carry NO index (only `registry_source`
and the `server_id` PK do -- see app/models.py and migrations 0010/0013). So
every GROUP BY on risk_tier/verdict is a full seq scan + hash aggregate over the
whole 596k-row table, and min/max(trust_score) is a full seq scan. Run several
of these concurrently on a 1-vCPU Fly PG and each holds a pooled connection for
the whole scan, so the pool drains and new requests 500.

THE FIX (additive, non-destructive)
------------------------------------
A plain btree on each of the three uncovered columns:
  * ix_registry_risk_tier  (risk_tier)   -> GROUP BY risk_tier becomes an
      index-only grouped scan; the "scored" COUNT (risk_tier IS NOT NULL) and the
      distribution both read it. Cardinality is tiny (a handful of tiers), so the
      index is small and the grouped aggregate collapses to a few index ranges.
  * ix_registry_verdict    (verdict)     -> same for the facets verdict GROUP BY.
  * ix_registry_trust_score(trust_score) -> min()/max() become a one-row btree
      endpoint lookup instead of a 596k-row scan (identical win to 0014's
      scored_at index for the freshness MIN/MAX). NB: the equal-width *banding*
      GROUP BY in compute_facets still visits every non-null row by nature
      (it buckets the whole column) -- that part is bounded by the 300s TTL +
      lifespan warm, not by this index; the index removes the separate min/max
      scan that preceded it.

Ops note (council ruling 2026-07-03, see 0006_axis_score_indexes / 0014): plain
CREATE INDEX takes a lock, and CONCURRENTLY cannot run inside alembic's
transaction; a hung migration in Fly's release_command blocks the whole deploy.
On PROD each index is therefore PRE-CREATED manually via `fly proxy` BEFORE the
deploy with (one per index):
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registry_risk_tier
    ON mcp_server_registry (risk_tier);
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registry_verdict
    ON mcp_server_registry (verdict);
  CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_registry_trust_score
    ON mcp_server_registry (trust_score);
so the `CREATE INDEX IF NOT EXISTS` below is a no-op on prod (the index already
exists) and never takes a blocking lock. Fresh/dev/CI databases (small) get the
indexes created here normally, in-transaction, which is fine at their size.

Postgres checks table OWNERSHIP before the IF NOT EXISTS short-circuit
(RangeVarCallbackOwnsRelation runs at table lookup), so the app-role migration
user fails on CREATE INDEX IF NOT EXISTS even when the index already exists
(2026-07-03 release_command failure). Guard with an explicit pg_indexes check,
exactly as 0006 does; sqlite (CI/dev) keeps the plain IF NOT EXISTS path.

Additive and reversible -- no column change, EXPAND-ONLY / GREEN.

PROD-SCHEMA CHANGE: this is a schema migration and requires chairman review
before it is applied/deployed. It is low-risk (additive, CONCURRENTLY-created on
prod, trivially reversible) but it IS a prod-schema change. Do not run it against
any live DB without that review.
"""
from alembic import op

revision = "0016_registry_facet_indexes"
down_revision = "0015_freshness_stats"
branch_labels = None
depends_on = None

# (index_name, table (columns)) -- the three mcp_server_registry columns the
# dashboard-summary and facet aggregates GROUP BY / min-max on that carry no
# index today. registry_source is already indexed (app/models.py index=True).
_INDEXES = (
    ("ix_registry_risk_tier", "mcp_server_registry (risk_tier)"),
    ("ix_registry_verdict", "mcp_server_registry (verdict)"),
    ("ix_registry_trust_score", "mcp_server_registry (trust_score)"),
)


def upgrade():
    conn = op.get_bind()
    for name, spec in _INDEXES:
        if conn.dialect.name == "postgresql":
            exists = conn.exec_driver_sql(
                "SELECT 1 FROM pg_indexes WHERE indexname = %s", (name,)
            ).first()
            if exists:
                continue
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {spec}")


def downgrade():
    for name, _spec in _INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
