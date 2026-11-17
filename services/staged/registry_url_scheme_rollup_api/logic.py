from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry


_SCHEME_ORDER = (
    "https",
    "http",
    "stdio",
    "sse",
    "websocket",
    "unix",
    "other",
    "missing_url",
)


def _scheme_bucket(url: str | None) -> str:
    if url is None or not str(url).strip():
        return "missing_url"

    value = str(url).strip()
    try:
        scheme = urlsplit(value).scheme.lower()
    except ValueError:
        scheme = ""

    if scheme == "https":
        return "https"
    if scheme == "http":
        return "http"
    if scheme == "stdio":
        return "stdio"
    if scheme == "sse":
        return "sse"
    if scheme in {"ws", "wss", "websocket"}:
        return "websocket"
    if scheme == "unix":
        return "unix"
    return "other"


async def get_url_scheme_rollup(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.url,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            McpServerRegistry.trust_score,
        )
    )

    scheme_counts: Counter[str] = Counter()
    insecure_tiers: Counter[str] = Counter()

    for row in result.all():
        scheme = _scheme_bucket(row.url)
        scheme_counts[scheme] += 1

        if scheme in {"http", "missing_url"}:
            risk_tier = row.risk_tier.strip() if row.risk_tier else ""
            insecure_tiers[risk_tier or "unknown"] += 1

    total = sum(scheme_counts.values())
    by_scheme = [
        {
            "scheme": scheme,
            "count": scheme_counts[scheme],
            "pct": round((scheme_counts[scheme] / total) * 100, 1) if total else 0.0,
        }
        for scheme in _SCHEME_ORDER
        if scheme_counts[scheme]
    ]

    insecure_by_tier = [
        {"risk_tier": risk_tier, "count": count}
        for risk_tier, count in sorted(insecure_tiers.items())
    ]

    return {
        "total": total,
        "by_scheme": by_scheme,
        "insecure_by_tier": insecure_by_tier,
        "insecure_total": sum(insecure_tiers.values()),
        "missing_url_total": scheme_counts["missing_url"],
    }


get_registry_url_scheme_rollup = get_url_scheme_rollup


async def _self_test() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(McpServerRegistry.metadata.create_all)

    seeded_servers = [
        {
            "server_id": "scheme-test-1",
            "name": "HTTPS One",
            "url": "https://one.example/mcp",
            "registry_source": "test",
            "risk_tier": "high",
            "verdict": "allow",
            "trust_score": 90,
        },
        {
            "server_id": "scheme-test-2",
            "name": "HTTPS Two",
            "url": "https://two.example/mcp",
            "registry_source": "test",
            "risk_tier": "low",
            "verdict": "allow",
            "trust_score": 95,
        },
        {
            "server_id": "scheme-test-3",
            "name": "HTTP One",
            "url": "http://three.example/mcp",
            "registry_source": "test",
            "risk_tier": "critical",
            "verdict": "block",
            "trust_score": 20,
        },
        {
            "server_id": "scheme-test-4",
            "name": "HTTP Two",
            "url": "http://four.example/mcp",
            "registry_source": "test",
            "risk_tier": "high",
            "verdict": "review",
            "trust_score": 45,
        },
        {
            "server_id": "scheme-test-5",
            "name": "Stdio",
            "url": "stdio:///opt/mcp/server",
            "registry_source": "test",
            "risk_tier": "low",
            "verdict": "allow",
            "trust_score": 85,
        },
        {
            "server_id": "scheme-test-6",
            "name": "SSE",
            "url": "sse://six.example/events",
            "registry_source": "test",
            "risk_tier": "medium",
            "verdict": "review",
            "trust_score": 70,
        },
        {
            "server_id": "scheme-test-7",
            "name": "Missing URL",
            "url": None,
            "registry_source": "test",
            "risk_tier": "critical",
            "verdict": "block",
            "trust_score": 15,
        },
    ]

    async with session_factory() as session:
        session.add_all(McpServerRegistry(**server) for server in seeded_servers)
        await session.commit()

    test_app = FastAPI()

    async def override_session():
        async with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/registry/url-scheme-rollup")
    async def route(session: AsyncSession = Depends(get_session)):
        return await get_url_scheme_rollup(session)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/api/registry/url-scheme-rollup")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 7
    assert {entry["scheme"] for entry in payload["by_scheme"]} >= {
        "https",
        "http",
        "stdio",
        "sse",
        "missing_url",
    }
    assert payload["insecure_total"] == 3
    assert payload["missing_url_total"] == 1
    assert sum(entry["count"] for entry in payload["insecure_by_tier"]) == 3

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_self_test())
    print("PASS")