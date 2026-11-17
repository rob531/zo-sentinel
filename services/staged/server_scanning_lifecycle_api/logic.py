"""Data and computation layer for the server scanning lifecycle API."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp, treating naive timestamps as UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lifecycle_derived_fields(
    last_scanned: Any,
    last_seen: Any,
    now: Optional[datetime] = None,
) -> tuple[bool, Optional[int]]:
    """Derive scan freshness and elapsed seen days from registry timestamps."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    scanned_at = _parse_timestamp(last_scanned)
    seen_at = _parse_timestamp(last_seen)

    scan_stale = (
        scanned_at is not None
        and current_time - scanned_at > timedelta(days=7)
    )

    days_since_seen: Optional[int] = None
    if seen_at is not None:
        elapsed = current_time - seen_at
        days_since_seen = max(0, elapsed.days)

    return scan_stale, days_since_seen


def get_server_lifecycle(
    db: Session,
    server_id: str,
) -> Optional[dict[str, Any]]:
    """Return the projected lifecycle state for one registered server."""
    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.trust_score,
        McpServerRegistry.verdict,
        McpServerRegistry.confidence,
        McpServerRegistry.risk_tier,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.last_assessed,
        McpServerRegistry.scan_count,
    ).where(McpServerRegistry.server_id == server_id)

    row = db.execute(statement).mappings().one_or_none()
    if row is None:
        return None

    result = dict(row)
    scan_stale, days_since_seen = _lifecycle_derived_fields(
        result["last_scanned"],
        result["last_seen"],
    )
    result["scan_stale"] = scan_stale
    result["days_since_seen"] = days_since_seen
    return result


def get_lifecycle(
    db: Session,
    server_id: str,
) -> Optional[dict[str, Any]]:
    """Compatibility alias for the lifecycle lookup."""
    return get_server_lifecycle(db, server_id)


if __name__ == "__main__":
    now = datetime(2025, 1, 31, tzinfo=timezone.utc)
    stale, days = _lifecycle_derived_fields(
        "2024-12-22T00:00:00+00:00",
        "2025-01-30T00:00:00+00:00",
        now=now,
    )
    assert stale is True
    assert days == 1
    print("PASS")