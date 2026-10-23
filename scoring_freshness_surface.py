"""Public scoring-freshness transparency surface (chairman-built 2026-07-14).

PURPOSE: pipeline-watch CHECK B (moat freshness) was blind for 12 consecutive
runs -- tower :5432 refused and no external surface exposed scoring recency.
This router publishes AGGREGATE-ONLY freshness counts so the watcher (and any
user) can verify the scoring corpus is alive without credentials.

THE LINE (council roadmap Appendix H): no signed/keyed surface on stale data.
This endpoint is what MEASURES staleness -- aggregate counts and timestamps
only. No server names, no per-server rows, nothing signed or keyed.

LATENCY (fixed 2026-07-14 PM): this endpoint took **48s** in prod. Three seq
scans over mcp_llm_axis_scores (465,955 rows) -- COUNT(*), COUNT(DISTINCT
server_id) -- plus a COUNT(*) on the 80k registry, on a small Fly PG. It
returned correct data but blew through every sane client timeout, which
re-blinded the very CHECK B it was built to unblind. Tonight's rescore adds
~14k servers, so it was getting worse, not better.

Fix: a process-local TTL cache. These are corpus-wide aggregates that only
move when a rescore lands (weekly) -- serving a <=10-minute-old count is
honest and correct. The cache stores the FULL payload including its own
computed_at, and we surface `cache_age_seconds` so a caller can always see
exactly how old the numbers are. We do not pretend they are live.

Degrade honestly: on DB error we raise rather than emit a fabricated zero.
A fake zero here is indistinguishable from "the corpus is empty" -- the exact
class of silent lie freshness_gate.py exists to prevent (an unknown is not a
zero).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from app.read_surface_guard import SharedSnapshot

router = APIRouter(tags=["freshness"])

# Aggregates move only when a rescore lands (weekly); a cold compute is ~8min on
# prod, so the shared snapshot refreshes every 30 min and a 60-min TTL only
# flags stale if two refreshes in a row failed -- still vastly fresher than the
# 7-day SLA these numbers are measured against.
CACHE_TTL_SECONDS = 3600


def _compute(db: Session) -> dict:
    scores_rows = db.scalar(select(func.count()).select_from(McpLlmAxisScore)) or 0
    # scored_servers = distinct scored server_ids. COUNT(DISTINCT server_id) over
    # the 4.7M-row append-only score table forces a sort (measured 24-56s on prod
    # PG16); COUNT(*) over a GROUP BY server_id hash-aggregates off the
    # (server_id, ...) unique index as an index-only scan (~5s, same answer).
    # FU-667: same count, ~10x cheaper.
    scored_servers = db.scalar(
        select(func.count()).select_from(
            select(McpLlmAxisScore.server_id)
            .group_by(McpLlmAxisScore.server_id).subquery())) or 0
    registry_rows = db.scalar(select(func.count()).select_from(McpServerRegistry)) or 0
    # never_scored is the coverage hole: registry servers with NO score at all.
    # It MUST be the set difference (registry server_ids NOT IN scores), NOT the
    # arithmetic proxy `registry_rows - scored_servers`. The proxy only equals
    # the truth when every scored server_id is also a registry row, which is not
    # guaranteed: McpLlmAxisScore.server_id has no FK to the registry and the
    # score table is append-only, so an ORPHAN score (scored, since re-promoted
    # under a fresh server_id, or deleted from the registry) silently credits
    # the subtraction and drifts the published gap. This is the same anti-join
    # the sibling `/servers/never-scored` surface (never_scored_backlog_api.py)
    # already uses -- one aggregate COUNT, no per-server rows, THE LINE honoured.
    #
    # NOT EXISTS, not NOT IN (FU-667 / #6985). Postgres can only run
    # `NOT IN (subquery)` as a SubPlan: hashed while the distinct server_id set
    # fits in work_mem, otherwise rescanned once per registry row (~registry x
    # scored). That is a cliff, not a slope. Measured on PG16, migrated schema,
    # 300k registry / 1.6M score rows / 200k scored servers: work_mem=4MB ->
    # plain SubPlan, cancelled at 90s; 16MB -> hashed. NOT EXISTS at 4MB plans
    # as a hash anti-join: 0.5s, same count.
    has_score = (select(McpLlmAxisScore.server_id)
                 .where(McpLlmAxisScore.server_id == McpServerRegistry.server_id)
                 .exists())
    never_scored = db.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(~has_score)) or 0
    newest = db.scalar(select(func.max(McpLlmAxisScore.scored_at)))
    oldest = db.scalar(select(func.min(McpLlmAxisScore.scored_at)))
    return {
        "scores_rows": int(scores_rows),
        "scored_servers": int(scored_servers),
        "registry_rows": int(registry_rows),
        "never_scored": int(never_scored),
        "newest_scored_at": newest.isoformat() if newest else None,
        "oldest_scored_at": oldest.isoformat() if oldest else None,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


# FU-667: the aggregates above cost ~460s cold on the 1-vCPU prod PG -- more
# than any client waits and more than a request-time statement_timeout allows.
# A per-worker background compute multiplied that heavy scan across gunicorn
# workers and saturated the PG (the v122 pile-up that forced a rollback). So a
# SINGLE elected worker (Postgres advisory lock) recomputes on a cadence and
# writes freshness_stats; this route reads that one row O(1). 503 "warming"
# until the first compute lands -- an unknown is never a fabricated zero.
# advisory_lock_key: a fixed per-surface integer so at most one compute runs
# fleet-wide (FU-667 keys live in the 667_0xx band).
_snapshot = SharedSnapshot(
    "scoring_freshness", _compute, SessionLocal, advisory_lock_key=667001,
    ttl_seconds=CACHE_TTL_SECONDS, refresh_interval_seconds=1800)


@router.get("/freshness")
def scoring_freshness(db: Session = Depends(get_session)) -> dict:
    return _snapshot.get(db)
