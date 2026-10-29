from typing import Any, List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry


class FreshnessBucket(BaseModel):
    bucket: str
    count: int


class FreshnessTierBucket(BaseModel):
    risk_tier: Optional[str]
    bucket: str
    count: int


class StalestServer(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    days_since_last_seen: int
    scan_count: Optional[int]


class RegistryLastSeenFreshnessResponse(BaseModel):
    days: int
    buckets: List[FreshnessBucket]
    by_tier: List[FreshnessTierBucket]
    stalest: List[StalestServer]


_BUCKETS_QUERY = text(
    """
    WITH aged AS (
        SELECT
            server_id,
            name,
            risk_tier,
            scan_count,
            CASE
                WHEN last_seen IS NULL THEN '30d+'
                WHEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 <= 1
                    THEN '0-1d'
                WHEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 <= 7
                    THEN '1-7d'
                WHEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 <= 30
                    THEN '7-30d'
                ELSE '30d+'
            END AS bucket
        FROM mcp_server_registry
    )
    SELECT
        bucket,
        COUNT(*) AS count
    FROM aged
    GROUP BY bucket
    ORDER BY CASE bucket
        WHEN '0-1d' THEN 1
        WHEN '1-7d' THEN 2
        WHEN '7-30d' THEN 3
        WHEN '30d+' THEN 4
        ELSE 5
    END
    """
)

_BY_TIER_QUERY = text(
    """
    WITH aged AS (
        SELECT
            risk_tier,
            CASE
                WHEN last_seen IS NULL THEN '30d+'
                WHEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 <= 1
                    THEN '0-1d'
                WHEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 <= 7
                    THEN '1-7d'
                WHEN EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 <= 30
                    THEN '7-30d'
                ELSE '30d+'
            END AS bucket
        FROM mcp_server_registry
    )
    SELECT
        risk_tier,
        bucket,
        COUNT(*) AS count
    FROM aged
    GROUP BY risk_tier, bucket
    ORDER BY
        risk_tier NULLS FIRST,
        CASE bucket
            WHEN '0-1d' THEN 1
            WHEN '1-7d' THEN 2
            WHEN '7-30d' THEN 3
            WHEN '30d+' THEN 4
            ELSE 5
        END
    """
)

_STALEST_QUERY = text(
    """
    SELECT
        server_id,
        name,
        risk_tier,
        scan_count,
        EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - last_seen)) / 86400 AS age_days
    FROM mcp_server_registry
    ORDER BY last_seen ASC NULLS FIRST, server_id ASC
    LIMIT 10
    """
)


def _days_since_last_seen(value: Any) -> int:
    if value is None:
        return 999999
    try:
        age = float(value)
    except (TypeError, ValueError):
        return 999999
    if age < 0:
        return 0
    return int(age)


def get_registry_last_seen_freshness(
    days: int = 30,
    session: Any = Depends(get_session),
) -> RegistryLastSeenFreshnessResponse:
    if days < 1:
        raise ValueError("days must be a positive integer")

    bucket_rows = session.execute(_BUCKETS_QUERY).mappings().all()
    tier_rows = session.execute(_BY_TIER_QUERY).mappings().all()
    stale_rows = session.execute(_STALEST_QUERY).mappings().all()

    buckets = [
        FreshnessBucket(bucket=row["bucket"], count=int(row["count"]))
        for row in bucket_rows
    ]
    by_tier = [
        FreshnessTierBucket(
            risk_tier=row["risk_tier"],
            bucket=row["bucket"],
            count=int(row["count"]),
        )
        for row in tier_rows
    ]
    stalest = [
        StalestServer(
            server_id=str(row["server_id"]),
            name=row["name"],
            risk_tier=row["risk_tier"],
            days_since_last_seen=_days_since_last_seen(row["age_days"]),
            scan_count=(
                int(row["scan_count"])
                if row["scan_count"] is not None
                else None
            ),
        )
        for row in stale_rows
    ]

    return RegistryLastSeenFreshnessResponse(
        days=days,
        buckets=buckets,
        by_tier=by_tier,
        stalest=stalest,
    )


def get_last_seen_freshness(
    days: int = 30,
    session: Any = Depends(get_session),
) -> RegistryLastSeenFreshnessResponse:
    return get_registry_last_seen_freshness(days=days, session=session)


def registry_last_seen_freshness(
    days: int = 30,
    session: Any = Depends(get_session),
) -> RegistryLastSeenFreshnessResponse:
    return get_registry_last_seen_freshness(days=days, session=session)


def get_registry_freshness(
    days: int = 30,
    session: Any = Depends(get_session),
) -> RegistryLastSeenFreshnessResponse:
    return get_registry_last_seen_freshness(days=days, session=session)


if __name__ == "__main__":
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _Session:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "GROUP BY bucket" in sql:
                return _Result(
                    [
                        {"bucket": "0-1d", "count": 1},
                        {"bucket": "1-7d", "count": 1},
                        {"bucket": "30d+", "count": 1},
                    ]
                )
            if "GROUP BY risk_tier, bucket" in sql:
                return _Result(
                    [
                        {"risk_tier": "high", "bucket": "0-1d", "count": 1},
                        {"risk_tier": "medium", "bucket": "1-7d", "count": 1},
                        {"risk_tier": "low", "bucket": "30d+", "count": 1},
                    ]
                )
            return _Result(
                [
                    {
                        "server_id": "server-3",
                        "name": "stale-server",
                        "risk_tier": "low",
                        "scan_count": 3,
                        "age_days": 40,
                    },
                    {
                        "server_id": "server-2",
                        "name": "older-server",
                        "risk_tier": "medium",
                        "scan_count": 8,
                        "age_days": 10,
                    },
                    {
                        "server_id": "server-1",
                        "name": "fresh-server",
                        "risk_tier": "high",
                        "scan_count": 12,
                        "age_days": 0,
                    },
                ]
            )

    result = get_registry_last_seen_freshness(days=30, session=_Session())
    assert result.days == 30
    assert sum(item.count for item in result.buckets) == 3
    assert result.stalest[0].server_id == "server-3"
    assert result.stalest[0].days_since_last_seen >= result.stalest[1].days_since_last_seen
    print("PASS")