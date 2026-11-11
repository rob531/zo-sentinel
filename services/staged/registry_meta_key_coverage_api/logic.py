from __future__ import annotations

import json
from typing import Any

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


class MetaKeyCoverage(BaseModel):
    key: str
    count: int
    pct: float


class RegistryMetaKeyCoverageResponse(BaseModel):
    total_servers: int
    keys: list[MetaKeyCoverage] = Field(default_factory=list)
    missing_meta_count: int


MetaKeyCount = MetaKeyCoverage
MetaKeyCoverageResponse = RegistryMetaKeyCoverageResponse

_REGISTRY_TABLE = McpServerRegistry.__tablename__

_POSTGRES_COVERAGE_SQL = """
WITH top_servers AS (
    SELECT server_id, name, risk_tier, meta
    FROM mcp_server_registry
    ORDER BY scan_count DESC NULLS LAST, server_id ASC
    LIMIT :limit
),
key_counts AS (
    SELECT
        jsonb_object_keys(COALESCE(meta, '{}'::jsonb)) AS key,
        COUNT(*)::bigint AS count
    FROM top_servers
    GROUP BY 1
),
totals AS (
    SELECT
        COUNT(*)::bigint AS total_servers,
        COUNT(*) FILTER (WHERE meta IS NULL)::bigint AS missing_meta_count
    FROM top_servers
)
SELECT
    totals.total_servers,
    totals.missing_meta_count,
    key_counts.key,
    key_counts.count
FROM totals
LEFT JOIN key_counts ON TRUE
ORDER BY key_counts.count DESC NULLS LAST, key_counts.key ASC
"""

_SQLITE_TOP_SERVERS_SQL = """
SELECT server_id, name, risk_tier, meta
FROM mcp_server_registry
ORDER BY scan_count DESC NULLS LAST, server_id ASC
LIMIT :limit
"""


def _decode_meta(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


async def get_meta_key_coverage(
    session: AsyncSession = Depends(get_session),
    limit: int = 100,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")

    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        result = await session.execute(
            text(_POSTGRES_COVERAGE_SQL),
            {"limit": limit},
        )
        rows = result.mappings().all()

        total_servers = int(rows[0]["total_servers"]) if rows else 0
        missing_meta_count = int(rows[0]["missing_meta_count"]) if rows else 0
        key_rows = [row for row in rows if row["key"] is not None]
    else:
        result = await session.execute(
            text(_SQLITE_TOP_SERVERS_SQL),
            {"limit": limit},
        )
        rows = result.mappings().all()

        total_servers = len(rows)
        missing_meta_count = sum(1 for row in rows if row["meta"] is None)
        key_counts: dict[str, int] = {}

        for row in rows:
            meta = _decode_meta(row["meta"])
            for key in meta:
                key_counts[key] = key_counts.get(key, 0) + 1

        key_rows = [
            {"key": key, "count": count}
            for key, count in sorted(
                key_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ]

    denominator = total_servers or 1
    keys = [
        {
            "key": str(row["key"]),
            "count": int(row["count"]),
            "pct": round((int(row["count"]) / denominator) * 100, 2),
        }
        for row in key_rows
    ]

    return {
        "total_servers": total_servers,
        "keys": keys,
        "missing_meta_count": missing_meta_count,
    }


async def registry_meta_key_coverage_endpoint(
    limit: int = Query(default=100, ge=1),
    session: AsyncSession = Depends(get_session),
) -> RegistryMetaKeyCoverageResponse:
    return RegistryMetaKeyCoverageResponse(
        **await get_meta_key_coverage(session=session, limit=limit)
    )


get_registry_meta_key_coverage = get_meta_key_coverage


if __name__ == "__main__":
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def run_self_test() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE mcp_server_registry (
                        server_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        risk_tier TEXT,
                        scan_count INTEGER NOT NULL DEFAULT 0,
                        meta JSON
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, risk_tier, scan_count, meta)
                    VALUES
                        (:server_id, :name, :risk_tier, :scan_count, :meta)
                    """
                ),
                [
                    {
                        "server_id": "server-1",
                        "name": "Server One",
                        "risk_tier": "high",
                        "scan_count": 30,
                        "meta": json.dumps({"tool_count": 4, "owner": "platform"}),
                    },
                    {
                        "server_id": "server-2",
                        "name": "Server Two",
                        "risk_tier": "medium",
                        "scan_count": 20,
                        "meta": json.dumps({"tool_count": 2}),
                    },
                    {
                        "server_id": "server-3",
                        "name": "Server Three",
                        "risk_tier": "low",
                        "scan_count": 10,
                        "meta": None,
                    },
                ],
            )

        local_app = FastAPI()

        @local_app.get(
            "/api/registry/meta-key-coverage",
            response_model=RegistryMetaKeyCoverageResponse,
        )
        async def meta_key_coverage_route(
            limit: int = Query(default=100, ge=1),
            session: AsyncSession = Depends(get_session),
        ) -> dict[str, Any]:
            return await get_meta_key_coverage(session=session, limit=limit)

        async def override_get_session():
            async with session_factory() as session:
                yield session

        local_app.dependency_overrides[get_session] = override_get_session

        try:
            with TestClient(local_app) as client:
                response = client.get(
                    "/api/registry/meta-key-coverage",
                    params={"limit": 10},
                )
                assert response.status_code == 200, response.text
                payload = response.json()
                assert payload["total_servers"] == 3, payload
                assert payload["missing_meta_count"] == 1, payload
                tool_count = next(
                    item for item in payload["keys"] if item["key"] == "tool_count"
                )
                assert tool_count["count"] == 2, payload
        finally:
            await engine.dispose()

    asyncio.run(run_self_test())
    print("PASS")