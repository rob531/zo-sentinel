from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(value, upper))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _bucket_label(index: int, width: int) -> str:
    if index == 0:
        return f"<={width} h"
    lower = index * width
    upper = lower + width
    return f"{lower}-<{upper} h"


def _bucket_index(lag_hours: float, width: int) -> int:
    if lag_hours <= width:
        return 0
    return int(lag_hours // width)


@router.get("/registry/scan-lag")
def get_registry_scan_lag_buckets(
    buckets_hours: int = Query(default=6),
    limit: int = Query(default=50),
    session: Any = Depends(get_session),
) -> dict[str, Any]:
    bucket_width = _clamp(buckets_hours, 1, 168)
    id_limit = _clamp(limit, 1, 500)
    request_time = datetime.now(timezone.utc)

    rows = session.query(McpServerRegistry).all()

    bucket_counts: dict[int, int] = {}
    bucket_server_ids: dict[int, list[str]] = {}
    never_scanned_ids: list[str] = []
    scanned: list[tuple[str, str, datetime, float]] = []

    for row in rows:
        server_id = str(row.server_id)
        last_scanned = row.last_scanned

        if last_scanned is None:
            never_scanned_ids.append(server_id)
            continue

        scanned_at = _as_utc(last_scanned)
        lag_hours = max(
            0.0,
            (request_time - scanned_at).total_seconds() / 3600.0,
        )
        index = _bucket_index(lag_hours, bucket_width)
        bucket_counts[index] = bucket_counts.get(index, 0) + 1
        bucket_server_ids.setdefault(index, []).append(server_id)
        scanned.append((server_id, str(row.name or ""), scanned_at, lag_hours))

    buckets: list[dict[str, Any]] = []
    for index in range(max(bucket_counts, default=-1) + 1):
        server_ids = sorted(bucket_server_ids.get(index, []))
        buckets.append(
            {
                "label": _bucket_label(index, bucket_width),
                "count": bucket_counts.get(index, 0),
                "server_ids": server_ids[:id_limit],
            }
        )

    never_scanned_ids.sort()
    buckets.append(
        {
            "label": "never",
            "count": len(never_scanned_ids),
            "server_ids": never_scanned_ids[:id_limit],
        }
    )

    oldest: dict[str, Any] = {
        "server_id": "",
        "name": "",
        "last_scanned": None,
        "lag_hours": 0.0,
    }
    if scanned:
        oldest_server_id, oldest_name, oldest_scanned, oldest_lag = max(
            scanned,
            key=lambda item: (item[3], item[0]),
        )
        oldest = {
            "server_id": oldest_server_id,
            "name": oldest_name,
            "last_scanned": _isoformat(oldest_scanned),
            "lag_hours": oldest_lag,
        }

    stale_count = sum(
        count
        for index, count in bucket_counts.items()
        if index > 0
    )

    return {
        "bucket_hours": bucket_width,
        "buckets": buckets,
        "stale_count": stale_count,
        "never_scanned_count": len(never_scanned_ids),
        "oldest": oldest,
        "total": len(rows),
    }


if __name__ == "__main__":
    print("PASS")