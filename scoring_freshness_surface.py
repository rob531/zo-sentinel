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
    newest = db.scalar(select(func.max(McpLlmAxisScore.scored_at)))
    oldest = db.scalar(select(func.min(McpLlmAxisScore.scored_at)))
    return {
        "scores_rows": int(scores_rows),
        "scored_servers": int(scored_servers),
        "registry_rows": int(registry_rows),
        # never_scored: the coverage hole. 80,539 - 66,565 = ~14k servers with
        # NO score at all as of 2026-07-14 -- a bigger product gap than staleness.
        "never_scored": max(0, int(registry_rows) - int(scored_servers)),
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
