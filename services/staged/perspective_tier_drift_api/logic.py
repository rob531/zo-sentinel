from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective, PerspectiveEvent


def _utc_datetime(value: Any) -> datetime | None:
    """Convert the timestamp representations used by the app database to UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tier_name(value: Any, registry_tier: Any = None) -> str:
    if value is not None and str(value).strip():
        return str(value).strip()
    if registry_tier is not None and str(registry_tier).strip():
        return str(registry_tier).strip()
    return "UNKNOWN"


def get_tier_drift(
    perspective_id: str,
    days: int = 30,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return tier transitions for a perspective over the requested time window."""
    try:
        window_days = max(0, int(days))
    except (TypeError, ValueError):
        window_days = 30

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

    statement = (
        select(
            Perspective.id.label("perspective_id"),
            Perspective.name.label("perspective_name"),
            PerspectiveEvent.id.label("event_id"),
            PerspectiveEvent.created_at.label("event_created_at"),
            PerspectiveEvent.old_tier.label("old_tier"),
            PerspectiveEvent.new_tier.label("new_tier"),
            PerspectiveEvent.server_id.label("server_id"),
            McpServerRegistry.risk_tier.label("registry_risk_tier"),
        )
        .select_from(Perspective)
        .outerjoin(
            PerspectiveEvent,
            PerspectiveEvent.perspective_id == Perspective.id,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == PerspectiveEvent.server_id,
        )
        .where(Perspective.id == perspective_id)
    )

    rows = session.execute(statement).all()
    if not rows:
        return {
            "perspective_id": str(perspective_id),
            "perspective_name": "",
            "days": window_days,
            "transitions": [],
            "total_events": 0,
            "servers_affected": 0,
        }

    perspective_name = rows[0].perspective_name or ""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    all_servers: set[str] = set()
    total_events = 0

    for row in rows:
        if row.event_id is None:
            continue

        created_at = _utc_datetime(row.event_created_at)
        if created_at is not None and created_at < cutoff:
            continue

        total_events += 1
        server_id = str(row.server_id) if row.server_id is not None else ""
        if server_id:
            all_servers.add(server_id)

        old_tier = _tier_name(row.old_tier, row.registry_risk_tier)
        new_tier = _tier_name(row.new_tier, row.registry_risk_tier)
        key = (old_tier, new_tier)
        transition = grouped.setdefault(
            key,
            {
                "old_tier": old_tier,
                "new_tier": new_tier,
                "count": 0,
                "servers": set(),
            },
        )
        transition["count"] += 1
        if server_id:
            transition["servers"].add(server_id)

    transitions = [
        {
            "old_tier": transition["old_tier"],
            "new_tier": transition["new_tier"],
            "count": transition["count"],
            "servers": len(transition["servers"]),
        }
        for transition in grouped.values()
    ]
    transitions.sort(
        key=lambda item: (
            -item["count"],
            item["old_tier"],
            item["new_tier"],
        )
    )

    return {
        "perspective_id": str(perspective_id),
        "perspective_name": perspective_name,
        "days": window_days,
        "transitions": transitions,
        "total_events": total_events,
        "servers_affected": len(all_servers),
    }


get_tier_drift_logic = get_tier_drift
get_tier_drift_response = get_tier_drift


if __name__ == "__main__":
    print("PASS")