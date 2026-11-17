from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


BUCKET_LABELS = (
    "0.0-0.25",
    "0.25-0.5",
    "0.5-0.75",
    "0.75-1.0",
    "unknown",
)


class ConfidenceBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)
    avg_trust_score: float


class ConfidenceBucketsResponse(BaseModel):
    buckets: list[ConfidenceBucket]
    total: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    as_of: str


async def get_confidence_buckets(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    bucket = case(
        (McpServerRegistry.confidence.is_(None), "unknown"),
        (McpServerRegistry.confidence < 0.25, "0.0-0.25"),
        (McpServerRegistry.confidence < 0.5, "0.25-0.5"),
        (McpServerRegistry.confidence < 0.75, "0.5-0.75"),
        else_="0.75-1.0",
    )

    statement = (
        select(
            bucket.label("bucket"),
            func.count(McpServerRegistry.server_id).label("count"),
            func.avg(McpServerRegistry.trust_score).label("avg_trust_score"),
        )
        .select_from(McpServerRegistry)
        .group_by(bucket)
    )

    result = await session.execute(statement)
    grouped = {
        row.bucket: {
            "bucket": row.bucket,
            "count": int(row.count),
            "avg_trust_score": (
                float(row.avg_trust_score)
                if row.avg_trust_score is not None
                else 0.0
            ),
        }
        for row in result
    }

    buckets = [
        grouped.get(
            label,
            {"bucket": label, "count": 0, "avg_trust_score": 0.0},
        )
        for label in BUCKET_LABELS
    ]
    total = sum(item["count"] for item in buckets)
    low_confidence_count = buckets[0]["count"] + buckets[1]["count"]

    return {
        "buckets": buckets,
        "total": total,
        "low_confidence_count": low_confidence_count,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    test_engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SessionFactory = async_sessionmaker(
        test_engine,
        expire_on_commit=False,
    )

    async def initialize_database() -> None:
        async with test_engine.begin() as connection:
            await connection.run_sync(McpServerRegistry.__table__.create)

    async def seed_database() -> None:
        async with SessionFactory() as session:
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="confidence-test-1",
                        confidence=0.1,
                        trust_score=0.2,
                    ),
                    McpServerRegistry(
                        server_id="confidence-test-2",
                        confidence=0.25,
                        trust_score=0.4,
                    ),
                    McpServerRegistry(
                        server_id="confidence-test-3",
                        confidence=0.8,
                        trust_score=0.9,
                    ),
                    McpServerRegistry(
                        server_id="confidence-test-4",
                        confidence=None,
                        trust_score=0.5,
                    ),
                ]
            )
            await session.commit()

    async def override_get_session():
        async with SessionFactory() as session:
            yield session

    test_app = FastAPI()

    @test_app.get(
        "/api/registry/confidence-buckets",
        response_model=ConfidenceBucketsResponse,
    )
    async def confidence_buckets_endpoint(
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        return await get_confidence_buckets(session)

    test_app.dependency_overrides[get_session] = override_get_session

    import asyncio

    asyncio.run(initialize_database())
    asyncio.run(seed_database())

    with TestClient(test_app) as client:
        response = client.get("/api/registry/confidence-buckets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        unknown = next(
            item for item in payload["buckets"] if item["bucket"] == "unknown"
        )
        assert unknown["count"] == 1
        assert sum(item["count"] for item in payload["buckets"]) == payload["total"]

    asyncio.run(test_engine.dispose())
    print("PASS")