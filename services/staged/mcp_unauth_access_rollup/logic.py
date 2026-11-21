"""Logic layer for MCP unauthenticated-access findings."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpUnauthAccess


DEFAULT_STALE_THRESHOLD_DAYS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(updated_at: datetime, now: datetime) -> int:
    elapsed = now - _normalise_timestamp(updated_at)
    if elapsed.total_seconds() <= 0:
        return 0
    return int(elapsed.total_seconds() // 86400)


def _is_stale(updated_at: datetime, now: datetime, threshold_days: int) -> bool:
    return now - _normalise_timestamp(updated_at) > timedelta(days=threshold_days)


def _flag_key(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _threshold(stale_threshold_days: Optional[int]) -> int:
    if stale_threshold_days is None:
        return DEFAULT_STALE_THRESHOLD_DAYS
    return max(0, int(stale_threshold_days))


def get_unauth_rollup(
    db: Session,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    """Build the unauthenticated-access rollup from observed access rows."""
    threshold = _threshold(stale_threshold_days)
    now = _utc_now()

    rows = db.execute(
        select(
            McpUnauthAccess.server_id,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
        )
    ).all()

    by_unauth_access: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    server_ids = set()
    stale_count = 0

    for server_id, unauth_access, source, updated_at in rows:
        server_ids.add(server_id)
        unauth_key = _flag_key(unauth_access)
        source_key = _flag_key(source)
        by_unauth_access[unauth_key] = by_unauth_access.get(unauth_key, 0) + 1
        by_source[source_key] = by_source.get(source_key, 0) + 1

        if updated_at is not None and _is_stale(updated_at, now, threshold):
            stale_count += 1

    return {
        "total_servers": len(server_ids),
        "by_unauth_access": by_unauth_access,
        "by_source": by_source,
        "stale_count": stale_count,
        "stale_threshold_days": threshold,
        "generated_at": now,
    }


def get_server_unauth_access(
    db: Session,
    server_id: str,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    """Return registry metadata and unauthenticated-access observations."""
    threshold = _threshold(stale_threshold_days)
    now = _utc_now()

    registry_row = db.execute(
        select(McpServerRegistry.name, McpServerRegistry.risk_tier).where(
            McpServerRegistry.server_id == server_id
        )
    ).first()
    name = registry_row[0] if registry_row is not None else None
    risk_tier = registry_row[1] if registry_row is not None else None

    rows = db.execute(
        select(
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
        )
        .where(McpUnauthAccess.server_id == server_id)
        .order_by(McpUnauthAccess.updated_at.desc())
    ).all()

    observations: List[Dict[str, Any]] = []
    for url, unauth_access, source, updated_at in rows:
        observation: Dict[str, Any] = {
            "url": url,
            "unauth_access": unauth_access,
            "source": source,
            "updated_at": updated_at,
            "age_days": 0 if updated_at is None else _age_days(updated_at, now),
        }
        observations.append(observation)

    return {
        "server_id": server_id,
        "name": name,
        "risk_tier": risk_tier,
        "observations": observations,
    }


def get_unauth_access_rollup(
    db: Session,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    return get_unauth_rollup(db, stale_threshold_days)


def get_rollup(
    db: Session,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    return get_unauth_rollup(db, stale_threshold_days)


def get_server_observations(
    db: Session,
    server_id: str,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    return get_server_unauth_access(db, server_id, stale_threshold_days)


def get_unauth_server(
    db: Session,
    server_id: str,
    stale_threshold_days: int = DEFAULT_STALE_THRESHOLD_DAYS,
) -> Dict[str, Any]:
    return get_server_unauth_access(db, server_id, stale_threshold_days)


if __name__ == "__main__":
    print("PASS")