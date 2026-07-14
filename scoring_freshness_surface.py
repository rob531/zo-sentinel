"""Public scoring-freshness transparency surface (chairman-built 2026-07-14).

PURPOSE: pipeline-watch CHECK B (moat freshness) was blind for 12 consecutive
runs -- tower :5432 refused and no external surface exposed scoring recency.
This router publishes AGGREGATE-ONLY freshness counts so the watcher (and any
user) can verify the scoring corpus is alive without credentials.

THE LINE (council roadmap Appendix H): no signed/keyed surface on stale data.
This endpoint is what MEASURES staleness -- aggregate counts and timestamps
only. No server names, no per-server rows, nothing signed or keyed.
(Factory retries of this surface never landed: freshness_transparency_api,
server_freshness_public_api, scoring_freshness_surface all died pre-PR.)
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(tags=["freshness"])


@router.get("/freshness")
def scoring_freshness(db: Session = Depends(get_session)) -> dict:
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
        "newest_scored_at": newest.isoformat() if newest else None,
        "oldest_scored_at": oldest.isoformat() if oldest else None,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }