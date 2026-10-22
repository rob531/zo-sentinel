from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpServerRegistry as Registry
except ImportError:
    try:
        from app.models import McpServerRegistry as Registry
    except ImportError:
        try:
            from app.models import McpServerRegistryModel as Registry
        except ImportError:
            from app.models import ServerRegistry as Registry


BUCKET_NAMES = (
    "never_scanned",
    "scanned_1h",
    "scanned_24h",
    "scanned_7d",
    "scanned_30d",
    "older_than_30d",
)

_ONE_HOUR = timedelta(hours=1)
_ONE_DAY = timedelta(days=1)
_SEVEN_DAYS = timedelta(days=7)
_THIRTY_DAYS = timedelta(days=30)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            return None

    if not isinstance(value, datetime):
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for(last_scanned: Any, now: datetime) -> str:
    scanned_at = _as_utc(last_scanned)
    if scanned_at is None:
        return "never_scanned"

    age = now - scanned_at
    if age <= _ONE_HOUR:
        return "scanned_1h"
    if age <= _ONE_DAY:
        return "scanned_24h"
    if age <= _SEVEN_DAYS:
        return "scanned_7d"
    if age <= _THIRTY_DAYS:
        return "scanned_30d"
    return "older_than_30d"


def _source_name(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    return text if text else "unknown"


def _iso8601(value: Any) -> Optional[str]:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def get_scan_recency(
    session: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current_time = _as_utc(now) if now is not None else _utc_now()
    if current_time is None:
        current_time = _utc_now()

    statement = select(
        Registry.server_id,
        Registry.name,
        Registry.last_seen,
        Registry.last_scanned,
        Registry.scan_count,
        Registry.registry_source,
        Registry.risk_tier,
    )

    rows = session.execute(statement).mappings().all()

    bucket_servers: Dict[str, set[str]] = {name: set() for name in BUCKET_NAMES}
    bucket_sources: Dict[str, Dict[str, set[str]]] = {
        name: {} for name in BUCKET_NAMES
    }

    for row in rows:
        server_id = row.get("server_id")
        if server_id is None:
            continue

        server_key = str(server_id)
        bucket = _bucket_for(row.get("last_scanned"), current_time)
        source = _source_name(row.get("registry_source"))

        bucket_servers[bucket].add(server_key)
        bucket_sources[bucket].setdefault(source, set()).add(server_key)

    buckets = []
    for bucket_name in BUCKET_NAMES:
        buckets.append(
            {
                "bucket": bucket_name,
                "count": len(bucket_servers[bucket_name]),
                "by_source": {
                    source: len(server_ids)
                    for source, server_ids in sorted(
                        bucket_sources[bucket_name].items()
                    )
                },
            }
        )

    seen_servers: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        server_id = row.get("server_id")
        last_seen = _as_utc(row.get("last_seen"))
        if server_id is None or last_seen is None:
            continue

        server_key = str(server_id)
        candidate = {
            "server_id": server_key,
            "name": str(row.get("name") or ""),
            "last_seen": last_seen.isoformat(),
            "_last_seen": last_seen,
        }

        previous = seen_servers.get(server_key)
        if previous is None or candidate["_last_seen"] < previous["_last_seen"]:
            seen_servers[server_key] = candidate

    oldest = sorted(
        seen_servers.values(),
        key=lambda item: (item["_last_seen"], item["server_id"]),
    )[:20]

    oldest_last_seen = [
        {
            "server_id": item["server_id"],
            "name": item["name"],
            "last_seen": item["last_seen"],
        }
        for item in oldest
    ]

    return {
        "buckets": buckets,
        "oldest_last_seen": oldest_last_seen,
    }


def get_scan_recency_data(
    session: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    return get_scan_recency(session, now=now)


def get_scan_recency_summary(
    session: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    return get_scan_recency(session, now=now)


def get_scan_recency_report(
    session: Session,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    return get_scan_recency(session, now=now)


def _run_self_test() -> None:
    current_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    cases = (
        (None, "never_scanned"),
        (current_time - timedelta(minutes=30), "scanned_1h"),
        (current_time - timedelta(hours=2), "scanned_24h"),
        (current_time - timedelta(days=2), "scanned_7d"),
        (current_time - timedelta(days=10), "scanned_30d"),
        (current_time - timedelta(days=31), "older_than_30d"),
    )
    for timestamp, expected in cases:
        actual = _bucket_for(timestamp, current_time)
        assert actual == expected, (timestamp, expected, actual)

    assert len(BUCKET_NAMES) == 6
    assert len(set(BUCKET_NAMES)) == 6


if __name__ == "__main__":
    _run_self_test()
    print("PASS")