from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


async def get_corpus_gap(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    registry_result = await session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        ).order_by(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.name,
        )
    )

    registered: dict[str, dict[str, Any]] = {}
    for server_id, name, registry_source, risk_tier in registry_result.all():
        if server_id is not None and server_id not in registered:
            registered[server_id] = {
                "server_id": server_id,
                "name": name,
                "registry_source": registry_source,
                "risk_tier": risk_tier,
            }

    corpus_result = await session.execute(
        select(AskCorpusDoc.server_id).distinct()
    )
    indexed_ids = {
        server_id
        for server_id in corpus_result.scalars().all()
        if server_id is not None
    }

    source_servers: dict[Any, list[dict[str, Any]]] = {}
    for server in registered.values():
        source_servers.setdefault(server["registry_source"], []).append(server)

    by_source: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []

    for registry_source in sorted(
        source_servers,
        key=lambda value: "" if value is None else str(value),
    ):
        servers = source_servers[registry_source]
        indexed = sum(
            1 for server in servers if server["server_id"] in indexed_ids
        )
        by_source.append(
            {
                "registry_source": registry_source,
                "servers": len(servers),
                "indexed": indexed,
                "coverage_ratio": indexed / len(servers) if servers else 0.0,
            }
        )

        uncovered.extend(
            {
                "server_id": server["server_id"],
                "name": server["name"],
                "registry_source": server["registry_source"],
                "risk_tier": server["risk_tier"],
            }
            for server in servers
            if server["server_id"] not in indexed_ids
        )

    total_servers = len(registered)
    indexed_servers = sum(
        1 for server_id in registered if server_id in indexed_ids
    )

    uncovered.sort(
        key=lambda server: (
            "" if server["registry_source"] is None else str(server["registry_source"]),
            server["server_id"],
        )
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "total_servers": total_servers,
        "indexed_servers": indexed_servers,
        "coverage_ratio": (
            indexed_servers / total_servers if total_servers else 0.0
        ),
        "by_source": by_source,
        "uncovered": uncovered,
    }


get_ask_corpus_gap = get_corpus_gap
get_corpus_server_gap = get_corpus_gap
get_corpus_gap_data = get_corpus_gap
get_ask_corpus_server_gap = get_corpus_gap
get_server_gap_analysis = get_corpus_gap


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    class AsyncSessionAdapter:
        def __init__(self, session: Session) -> None:
            self._session = session

        async def execute(self, statement: Any) -> Any:
            return self._session.execute(statement)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id TEXT,
                    snippet TEXT,
                    terms TEXT,
                    content_hash TEXT,
                    indexed_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, risk_tier)
                VALUES
                    ('srv-01', 'Server One', 'source-a', 'high'),
                    ('srv-02', 'Server Two', 'source-a', 'medium'),
                    ('srv-03', 'Server Three', 'source-a', 'low'),
                    ('srv-04', 'Server Four', 'source-b', 'high'),
                    ('srv-05', 'Server Five', 'source-b', 'medium'),
                    ('srv-06', 'Server Six', 'source-b', 'low')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, snippet, terms, content_hash, indexed_at)
                VALUES
                    ('srv-01', 'one', 'alpha', 'hash-1', '2024-01-01T00:00:00Z'),
                    ('srv-04', 'four', 'beta', 'hash-2', '2024-01-02T00:00:00Z')
                """
            )
        )

    def override_get_session() -> AsyncIterator[AsyncSessionAdapter]:
        with Session(engine) as session:
            yield AsyncSessionAdapter(session)

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/ask/corpus/gap")
    async def corpus_gap_endpoint(
        session: AsyncSession = Depends(get_session),
    ) -> dict[str, Any]:
        return await get_corpus_gap(session)

    with TestClient(local_app) as client:
        response = client.get("/api/ask/corpus/gap")
        assert response.status_code == 200
        payload = response.json()

    assert payload["total_servers"] == 6
    assert payload["indexed_servers"] == 2
    assert 0.3 <= payload["coverage_ratio"] <= 0.4
    assert len(payload["by_source"]) == 2

    source_counts = {
        source["registry_source"]: (
            source["servers"],
            source["indexed"],
        )
        for source in payload["by_source"]
    }
    assert source_counts == {
        "source-a": (3, 1),
        "source-b": (3, 1),
    }

    uncovered_ids = {server["server_id"] for server in payload["uncovered"]}
    assert "srv-05" in uncovered_ids
    assert len(uncovered_ids) == 4

    print("PASS")


if __name__ == "__main__":
    _self_test()