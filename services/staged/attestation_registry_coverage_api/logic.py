from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


_ATTESTATION_COVERAGE_SQL = text(
    """
    WITH ranked_attestations AS (
        SELECT
            server_id,
            verdict,
            ROW_NUMBER() OVER (
                PARTITION BY server_id
                ORDER BY created_at DESC
            ) AS attestation_rank
        FROM mcp_attestations
        WHERE expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP
    )
    SELECT server_id, verdict
    FROM ranked_attestations
    WHERE attestation_rank = 1
    """
)


async def get_registry_coverage(session: AsyncSession) -> dict[str, Any]:
    registry_rows = (
        await session.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.registry_source,
                McpServerRegistry.verdict,
            )
        )
    ).all()

    attestation_rows = (
        await session.execute(_ATTESTATION_COVERAGE_SQL)
    ).all()

    attestations = {
        server_id: verdict
        for server_id, verdict in attestation_rows
    }

    totals: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {
            "total": 0,
            "attested": 0,
            "missing": 0,
        }
    )

    registry_total = 0
    attested = 0
    missing = 0
    verdict_mismatch = 0

    for server_id, registry_source, registry_verdict in registry_rows:
        source = str(registry_source) if registry_source is not None else "unknown"
        bucket = totals[source]
        bucket["total"] += 1
        registry_total += 1

        if server_id in attestations:
            attested += 1
            bucket["attested"] += 1

            if attestations[server_id] != registry_verdict:
                verdict_mismatch += 1
        else:
            missing += 1
            bucket["missing"] += 1

    by_source = [
        {
            "source": source,
            "total": totals[source]["total"],
            "attested": totals[source]["attested"],
            "missing": totals[source]["missing"],
        }
        for source in sorted(totals)
    ]

    return {
        "registry_total": registry_total,
        "attested": attested,
        "missing": missing,
        "verdict_mismatch": verdict_mismatch,
        "by_source": by_source,
    }


get_attestation_registry_coverage = get_registry_coverage


async def _self_test() -> None:
    import httpx
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as connection:
        await connection.run_sync(McpServerRegistry.__table__.create)
        await connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    server_id VARCHAR(255) NOT NULL,
                    verdict VARCHAR(64) NOT NULL,
                    created_at DATETIME NOT NULL,
                    expires_at DATETIME
                )
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO mcp_attestations
                    (server_id, verdict, created_at, expires_at)
                VALUES
                    (:server_id, :verdict, :created_at, :expires_at)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "verdict": "safe",
                    "created_at": "2025-01-01 00:00:00",
                    "expires_at": "2999-12-31 23:59:59",
                },
                {
                    "server_id": "server-2",
                    "verdict": "malicious",
                    "created_at": "2025-01-01 00:00:00",
                    "expires_at": "2999-12-31 23:59:59",
                },
            ],
        )

    async with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server 1",
                    url="https://server-1.example",
                    registry_source="github",
                    verdict="safe",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server 2",
                    url="https://server-2.example",
                    registry_source="github",
                    verdict="safe",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server 3",
                    url="https://server-3.example",
                    registry_source="pypi",
                    verdict="safe",
                ),
                McpServerRegistry(
                    server_id="server-4",
                    name="Server 4",
                    url="https://server-4.example",
                    registry_source="pypi",
                    verdict="safe",
                ),
            ]
        )
        await session.commit()

    async def override_get_session():
        async with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/attestation/registry-coverage")
    async def registry_coverage(
        session: AsyncSession = Depends(get_session),
    ):
        return await get_registry_coverage(session)

    transport = httpx.ASGITransport(app=that_app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/attestation/registry-coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["attested"] == 2, payload
        assert payload["missing"] == 2, payload
        assert payload["verdict_mismatch"] == 1, payload
        assert payload["registry_total"] == 4, payload

    await engine.dispose()
    print("PASS")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_self_test())