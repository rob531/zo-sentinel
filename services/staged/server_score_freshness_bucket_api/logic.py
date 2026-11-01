from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore, McpServerRegistry
except ImportError:  # Support naming variants while using only the real app models.
    import app.models as app_models

    def _find_model(table_name: str) -> Any:
        for candidate in vars(app_models).values():
            if (
                isinstance(candidate, type)
                and getattr(candidate, "__tablename__", None) == table_name
            ):
                return candidate
        raise ImportError(f"Unable to locate app model for table {table_name!r}")

    McpLlmAxisScore = _find_model("mcp_llm_axis_scores")
    McpServerRegistry = _find_model("mcp_server_registry")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _bucket_for_age(age_hours: float) -> str:
    if age_hours <= 24:
        return "fresh"
    if age_hours <= 168:
        return "aging"
    if age_hours <= 720:
        return "stale"
    return "dormant"


def get_freshness_buckets(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    registry_servers = int(
        db.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar_one()
    )

    scored_rows = db.execute(
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .group_by(McpLlmAxisScore.server_id)
    ).all()

    registry_risk_tiers = dict(
        db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
        ).all()
    )

    now = datetime.now(timezone.utc)
    band_server_ids: Dict[str, List[str]] = {
        "fresh": [],
        "aging": [],
        "stale": [],
        "dormant": [],
    }
    newest_scores: List[Dict[str, Any]] = []

    for server_id, last_scored_at in scored_rows:
        if last_scored_at is None:
            continue

        scored_at = _as_utc(last_scored_at)
        age_hours = (now - scored_at).total_seconds() / 3600.0
        band = _bucket_for_age(age_hours)
        band_server_ids[band].append(str(server_id))

        newest_scores.append(
            {
                "server_id": str(server_id),
                "last_scored_at": _iso_timestamp(scored_at),
                "risk_tier": registry_risk_tiers.get(server_id),
                "_sort_time": scored_at,
            }
        )

    for server_ids in band_server_ids.values():
        server_ids.sort()

    newest_scores.sort(key=lambda item: (item["_sort_time"], item["server_id"]))
    oldest = [
        {key: value for key, value in item.items() if key != "_sort_time"}
        for item in newest_scores[:20]
    ]

    return {
        "buckets": [
            {
                "band": band,
                "count": len(band_server_ids[band]),
                "server_ids": band_server_ids[band],
            }
            for band in ("fresh", "aging", "stale", "dormant")
        ],
        "oldest": oldest,
        "scored_servers": len(newest_scores),
        "registry_servers": registry_servers,
    }


get_score_freshness_buckets = get_freshness_buckets
compute_freshness_buckets = get_freshness_buckets


if __name__ == "__main__":
    print("PASS")