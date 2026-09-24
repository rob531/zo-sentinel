from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

router = APIRouter(prefix="/api", tags=["scoring"])


class ScoringSummaryResponse(BaseModel):
    total_scored: int
    axes: dict[str, int]
    never_scored_count: int
    freshness_buckets: dict[str, int]


async def get_scoring_summary(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get scoring summary with aggregations from mcp_llm_axis_scores and mcp_server_registry."""
    now = datetime.utcnow()

    total_scored_query = text("""
        SELECT COUNT(DISTINCT server_id)
        FROM mcp_llm_axis_scores
    """)
    result = await session.execute(total_scored_query)
    total_scored = result.scalar() or 0

    axes_query = text("""
        SELECT COALESCE(axis_name, 'unknown') as axis_name, COUNT(*)
        FROM mcp_llm_axis_scores
        GROUP BY axis_name
    """)
    result = await session.execute(axes_query)
    axes_result = [(row[0], row[1]) for row in result.fetchall()]

    never_scored_query = text("""
        SELECT COUNT(*)
        FROM mcp_server_registry r
        WHERE NOT EXISTS (
            SELECT 1 FROM mcp_llm_axis_scores s WHERE s.server_id = r.server_id
        )
    """)
    result = await session.execute(never_scored_query)
    never_scored_count = result.scalar() or 0

    freshness_query = text("""
        SELECT
            SUM(CASE WHEN scored_at > :threshold_1h THEN 1 ELSE 0 END) as lt_1h,
            SUM(CASE WHEN scored_at > :threshold_24h AND scored_at <= :threshold_1h THEN 1 ELSE 0 END) as h1_24,
            SUM(CASE WHEN scored_at > :threshold_7d AND scored_at <= :threshold_24h THEN 1 ELSE 0 END) as d1_7,
            SUM(CASE WHEN scored_at <= :threshold_7d THEN 1 ELSE 0 END) as gt_7d
        FROM mcp_llm_axis_scores
    """)
    result = await session.execute(freshness_query, {
        "threshold_1h": now - timedelta(hours=1),
        "threshold_24h": now - timedelta(hours=24),
        "threshold_7d": now - timedelta(days=7),
    })
    row = result.fetchone()
    freshness_buckets = {
        "<1h": row[0] or 0,
        "1-24h": row[1] or 0,
        "1-7d": row[2] or 0,
        ">7d": row[3] or 0,
    }

    return {
        "total_scored": total_scored,
        "axes": dict(axes_result),
        "never_scored_count": never_scored_count,
        "freshness_buckets": freshness_buckets,
    }


@router.get("/scoring/summary", response_model=ScoringSummaryResponse)
async def get_summary(
    session: AsyncSession = Depends(get_session),
) -> ScoringSummaryResponse:
    """Get scoring summary endpoint."""
    data = await get_scoring_summary(session)
    return ScoringSummaryResponse(**data)


def create_app() -> Any:
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    return app


if __name__ == "__main__":
    import asyncio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    async def run_test():
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        async with engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    url TEXT,
                    first_seen TIMESTAMP,
                    last_seen TIMESTAMP,
                    last_scanned TIMESTAMP,
                    last_assessed TIMESTAMP,
                    registry_source TEXT,
                    description TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    confidence REAL,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    scan_count INTEGER,
                    meta TEXT
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT NOT NULL,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    adapter_sha256 TEXT,
                    model_version TEXT,
                    decision_rule_version TEXT,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated INTEGER,
                    escalated_to TEXT,
                    scored_at TIMESTAMP,
                    UNIQUE(server_id, axis_name, adapter_sha256)
                )
            """))

        async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with async_session() as session:
            now = datetime.utcnow()
            await session.execute(text("""
                INSERT INTO mcp_server_registry (server_id, name, url) VALUES
                ('srv-001', 'Alpha Service', 'http://alpha.local'),
                ('srv-002', 'Beta Service', 'http://beta.local'),
                ('srv-003', 'Gamma Service', 'http://gamma.local')
            """))
            await session.execute(text("""
                INSERT INTO mcp_llm_axis_scores (server_id, axis_name, scored_at, adapter_sha256) VALUES
                ('srv-001', 'security', :t1, 'sha-alpha'),
                ('srv-001', 'reliability', :t1, 'sha-alpha'),
                ('srv-002', 'security', :t2, 'sha-beta'),
                ('srv-002', 'performance', :t3, 'sha-beta'),
                ('srv-003', 'compliance', :t4, 'sha-gamma')
            """), {
                "t1": now - timedelta(minutes=30),
                "t2": now - timedelta(hours=12),
                "t3": now - timedelta(days=3),
                "t4": now - timedelta(days=10),
            })
            await session.commit()

        async def override_get_session():
            async with async_session() as s:
                yield s

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = override_get_session

        client = TestClient(app)
        response = client.get("/api/scoring/summary")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()

        assert data["total_scored"] >= 1, f"total_scored >= 1 failed: {data['total_scored']}"
        assert data["never_scored_count"] >= 0, f"never_scored_count >= 0 failed: {data['never_scored_count']}"
        assert "freshness_buckets" in data

        for bucket in ["<1h", "1-24h", "1-7d", ">7d"]:
            assert bucket in data["freshness_buckets"], f"Missing bucket: {bucket}"

        print("PASS")

    asyncio.run(run_test())