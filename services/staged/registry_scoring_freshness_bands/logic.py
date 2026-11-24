"""Data and computation layer for registry scoring freshness bands."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


NEVER_SCORED = "NEVER_SCORED"
FRESH = "FRESH"
AGING = "AGING"
STALE = "STALE"

FRESHNESS_BANDS = (NEVER_SCORED, FRESH, AGING, STALE)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _band_for(last_scored_at: Optional[datetime], now: datetime) -> str:
    if last_scored_at is None:
        return NEVER_SCORED

    age = now - last_scored_at
    age_days = age.total_seconds() / 86400.0

    if age_days < 1.0:
        return FRESH
    if age_days <= 7.0:
        return AGING
    return STALE


def get_scoring_freshness_bands(
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    """Compute freshness bands for every server in the real registry."""
    latest_scores = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            latest_scores.c.last_scored_at,
        )
        .outerjoin(
            latest_scores,
            latest_scores.c.server_id == McpServerRegistry.server_id,
        )
    ).all()

    now = _utc_now()
    bands = {band: 0 for band in FRESHNESS_BANDS}
    by_source: Dict[str, Dict[str, int]] = {}
    servers: List[Dict[str, object]] = []

    for server_id, name, registry_source, scored_at in rows:
        source = registry_source or "unknown"
        last_scored_at = _as_utc(scored_at)
        band = _band_for(last_scored_at, now)

        bands[band] += 1
        source_bands = by_source.setdefault(
            source, {candidate: 0 for candidate in FRESHNESS_BANDS}
        )
        source_bands[band] += 1

        age_days: Optional[float]
        if last_scored_at is None:
            age_days = None
        else:
            age_days = round(
                max(0.0, (now - last_scored_at).total_seconds() / 86400.0),
                2,
            )

        servers.append(
            {
                "server_id": server_id,
                "name": name,
                "registry_source": registry_source,
                "last_scored_at": last_scored_at.isoformat()
                if last_scored_at is not None
                else None,
                "age_days": age_days,
            }
        )

    servers.sort(
        key=lambda server: (
            server["age_days"] is not None,
            -(server["age_days"] or 0.0),
            str(server["server_id"]),
        )
    )

    return {
        "bands": bands,
        "by_source": by_source,
        "oldest": servers[:50],
    }


def compute_scoring_freshness_bands(
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    """Compatibility alias for the registry freshness computation."""
    return get_scoring_freshness_bands(db)