from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


_BUCKETS = ("0-1d", "1-7d", "7-30d", "30-90d", "90d+")


def _normalise_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _bucket_for_age(age_seconds: float) -> str:
    age_days = max(0.0, age_seconds) / 86400.0
    if age_days < 1:
        return "0-1d"
    if age_days < 7:
        return "1-7d"
    if age_days < 30:
        return "7-30d"
    if age_days < 90:
        return "30-90d"
    return "90d+"


async def get_recency_histogram(
    days: int = 30,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be a positive integer")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    registry_result = await session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
    )
    registry = {
        str(row.server_id): {
            "server_id": str(row.server_id),
            "name": row.name,
            "risk_tier": row.risk_tier,
        }
        for row in registry_result.all()
    }

    association_result = await session.execute(
        text(
            """
            SELECT server_id, signal_type, severity, confidence, detected_at
            FROM mcp_threat_associations
            """
        )
    )

    bucket_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    server_counts: Counter[str] = Counter()
    total = 0

    for row in association_result.mappings().all():
        detected_at = _normalise_timestamp(row["detected_at"])
        if detected_at is None or detected_at < cutoff:
            continue

        server_id = str(row["server_id"])
        server = registry.get(server_id)
        if server is not None:
            server_id = server["server_id"]

        age_seconds = (now - detected_at).total_seconds()
        bucket_counts[_bucket_for_age(age_seconds)] += 1

        severity = row["severity"]
        severity_counts[str(severity) if severity is not None else "unknown"] += 1
        server_counts[server_id] += 1
        total += 1

    return {
        "days": days,
        "total": total,
        "buckets": [
            {"bucket": bucket, "count": bucket_counts.get(bucket, 0)}
            for bucket in _BUCKETS
        ],
        "by_severity": {
            severity: severity_counts[severity]
            for severity in sorted(severity_counts)
        },
        "servers": {
            server_id: server_counts[server_id]
            for server_id in sorted(server_counts)
        },
    }


get_threat_assoc_recency_histogram = get_recency_histogram
get_threat_associations_recency_histogram = get_recency_histogram


if __name__ == "__main__":
    from fastapi import FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    test_engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def create_test_store() -> None:
        async with test_engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE mcp_server_registry (
                        server_id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        risk_tier VARCHAR
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    CREATE TABLE mcp_threat_associations (
                        server_id VARCHAR NOT NULL,
                        signal_type VARCHAR,
                        severity VARCHAR,
                        confidence FLOAT,
                        detected_at TIMESTAMP
                    )
                    """
                )
            )

            await connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                    VALUES
                        ('server-a', 'Server A', 'critical'),
                        ('server-b', 'Server B', 'high'),
                        ('server-c', 'Server C', 'medium')
                    """
                )
            )

            seeded_at = datetime.now(timezone.utc)
            rows = [
                ("server-a", "malware", "critical", 0.95, seeded_at - timedelta(hours=12)),
                ("server-b", "phishing", "high", 0.85, seeded_at - timedelta(days=3)),
                ("server-c", "exploit", "medium", 0.70, seeded_at - timedelta(days=15)),
                ("server-a", "c2", "high", 0.80, seeded_at - timedelta(days=60)),
                ("server-b", "intrusion", "critical", 0.90, seeded_at - timedelta(days=120)),
            ]
            for server_id, signal_type, severity, confidence, detected_at in rows:
                await connection.execute(
                    text(
                        """
                        INSERT INTO mcp_threat_associations
                            (server_id, signal_type, severity, confidence, detected_at)
                        VALUES
                            (:server_id, :signal_type, :severity, :confidence, :detected_at)
                        """
                    ),
                    {
                        "server_id": server_id,
                        "signal_type": signal_type,
                        "severity": severity,
                        "confidence": confidence,
                        "detected_at": detected_at.isoformat(),
                    },
                )

    async def override_get_session():
        async with test_session_factory() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/threat-associations/recency-histogram")
    async def recency_histogram(
        days: int = Query(30, ge=1),
        session: AsyncSession = Depends(get_session),
    ):
        return await get_recency_histogram(days=days, session=session)

    import asyncio

    asyncio.run(create_test_store())

    with TestClient(that_app) as client:
        response = client.get(
            "/api/threat-associations/recency-histogram",
            params={"days": 365},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 5, payload
        assert sum(item["count"] for item in payload["buckets"]) == 5, payload
        assert "critical" in payload["by_severity"], payload

    asyncio.run(test_engine.dispose())
    print("PASS")