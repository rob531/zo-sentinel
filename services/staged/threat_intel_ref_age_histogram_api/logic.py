from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import ThreatIntelRef

BUCKETS = ("0-1d", "2-7d", "8-30d", "31-90d", "90d+")


def _get_age_bucket(fetched_at: datetime, now: datetime) -> str:
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    else:
        fetched_at = fetched_at.astimezone(timezone.utc)

    age_days = max((now - fetched_at).days, 0)
    if age_days < 2:
        return "0-1d"
    if age_days < 8:
        return "2-7d"
    if age_days < 31:
        return "8-30d"
    if age_days < 90:
        return "31-90d"
    return "90d+"


async def get_ref_age_histogram(
    days: int = 30,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await db.execute(
        select(ThreatIntelRef.fetched_at, ThreatIntelRef.is_aggregator)
    )
    now = datetime.now(timezone.utc)
    counts = {bucket: 0 for bucket in BUCKETS}
    aggregator_counts = {bucket: 0 for bucket in BUCKETS}

    for fetched_at, is_aggregator in result.all():
        bucket = _get_age_bucket(fetched_at, now)
        counts[bucket] += 1
        if is_aggregator:
            aggregator_counts[bucket] += 1

    return {
        "days": days,
        "buckets": [
            {
                "bucket": bucket,
                "count": counts[bucket],
                "aggregator_count": aggregator_counts[bucket],
            }
            for bucket in BUCKETS
        ],
        "total": sum(counts.values()),
    }


get_threat_intel_ref_age_histogram = get_ref_age_histogram


if __name__ == "__main__":
    import asyncio
    from datetime import timedelta

    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    async def self_test() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(ThreatIntelRef.__table__.create)

        now = datetime.now(timezone.utc)
        rows = [
            ThreatIntelRef(
                id=1,
                indicator_type="ip",
                indicator_value="192.0.2.1",
                pulse_id="pulse-1",
                pulse_name="Pulse 1",
                pulse_created=now,
                is_aggregator=False,
                source="self-test",
                source_url="https://example.invalid/1",
                fetched_at=now,
            ),
            ThreatIntelRef(
                id=2,
                indicator_type="ip",
                indicator_value="192.0.2.2",
                pulse_id="pulse-2",
                pulse_name="Pulse 2",
                pulse_created=now,
                is_aggregator=True,
                source="self-test",
                source_url="https://example.invalid/2",
                fetched_at=now - timedelta(days=3),
            ),
            ThreatIntelRef(
                id=3,
                indicator_type="domain",
                indicator_value="example.test",
                pulse_id="pulse-3",
                pulse_name="Pulse 3",
                pulse_created=now,
                is_aggregator=False,
                source="self-test",
                source_url="https://example.invalid/3",
                fetched_at=now - timedelta(days=10),
            ),
            ThreatIntelRef(
                id=4,
                indicator_type="url",
                indicator_value="https://example.test/4",
                pulse_id="pulse-4",
                pulse_name="Pulse 4",
                pulse_created=now,
                is_aggregator=True,
                source="self-test",
                source_url="https://example.invalid/4",
                fetched_at=now - timedelta(days=45),
            ),
            ThreatIntelRef(
                id=5,
                indicator_type="hash",
                indicator_value="a" * 64,
                pulse_id="pulse-5",
                pulse_name="Pulse 5",
                pulse_created=now,
                is_aggregator=False,
                source="self-test",
                source_url="https://example.invalid/5",
                fetched_at=now - timedelta(days=200),
            ),
        ]

        async with session_factory() as session:
            session.add_all(rows)
            await session.commit()

            local_app = FastAPI()
            local_app.dependency_overrides[get_session] = lambda: session

            @local_app.get("/api/threat-intel/ref-age-histogram")
            async def ref_age_histogram_endpoint(
                days: int = 30,
                db: AsyncSession = Depends(get_session),
            ) -> dict[str, Any]:
                return await get_ref_age_histogram(days=days, db=db)

            transport = httpx.ASGITransport(app=local_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/threat-intel/ref-age-histogram", params={"days": 30}
                )

            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 5
            assert sum(item["count"] for item in payload["buckets"]) == 5
            bucket_counts = {
                item["bucket"]: item["count"] for item in payload["buckets"]
            }
            assert bucket_counts["2-7d"] == 1
            assert bucket_counts["90d+"] == 1
            assert sum(
                item["aggregator_count"] for item in payload["buckets"]
            ) == 2

        async with engine.begin() as connection:
            await connection.run_sync(ThreatIntelRef.__table__.drop)
        await engine.dispose()

    asyncio.run(self_test())
    print("PASS")