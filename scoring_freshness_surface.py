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
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(tags=["freshness"])

# Aggregates move only when a rescore lands (weekly). 10 min is far fresher
# than the 7-day SLA these numbers are measured against.
CACHE_TTL_SECONDS = 600

_lock = threading.Lock()
_cache: dict | None = None
_cached_at: float = 0.0


def _compute(db: Session) -> dict:
    scores_rows = db.scalar(select(func.count()).select_from(McpLlmAxisScore)) or 0
    scored_servers = db.scalar(
        select(func.count(func.distinct(McpLlmAxisScore.server_id)))) or 0
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
    scored_ids = select(McpLlmAxisScore.server_id).distinct().scalar_subquery()
    never_scored = db.scalar(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.server_id.notin_(scored_ids))) or 0
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


@router.get("/freshness")
def scoring_freshness(db: Session = Depends(get_session)) -> dict:
    global _cache, _cached_at
    now = time.monotonic()
    with _lock:
        fresh_enough = _cache is not None and (now - _cached_at) < CACHE_TTL_SECONDS
        if not fresh_enough:
            # Let DB errors propagate: a 500 is honest, a fabricated zero is not.
            _cache = _compute(db)
            _cached_at = now
        payload = dict(_cache)
        payload["cache_age_seconds"] = round(now - _cached_at, 1)
        payload["cache_ttl_seconds"] = CACHE_TTL_SECONDS
    return payload
