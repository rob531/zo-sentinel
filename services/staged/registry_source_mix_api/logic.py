from collections import defaultdict
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def fetch_source_mix_rows(session: Session = Depends(get_session)) -> list[dict]:
    statement = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
    )
    return [dict(row) for row in session.execute(statement).mappings().all()]


def compute_source_mix(rows: list[dict]) -> dict:
    grouped: dict[str, dict[str, Any]] = {}
    total_servers = len(rows)

    for row in rows:
        source_value = row.get("registry_source")
        registry_source = "unknown" if source_value is None else str(source_value)

        if registry_source not in grouped:
            grouped[registry_source] = {
                "server_count": 0,
                "tier_counts": defaultdict(int),
                "trust_total": 0.0,
                "trust_count": 0,
            }

        source = grouped[registry_source]
        source["server_count"] += 1

        risk_tier = row.get("risk_tier")
        if risk_tier is not None:
            source["tier_counts"][str(risk_tier)] += 1

        trust_score = row.get("trust_score")
        if trust_score is not None:
            source["trust_total"] += float(trust_score)
            source["trust_count"] += 1

    sources = []
    for registry_source, source in sorted(
        grouped.items(),
        key=lambda item: (-item[1]["server_count"], item[0]),
    ):
        trust_count = source["trust_count"]
        sources.append(
            {
                "registry_source": registry_source,
                "server_count": source["server_count"],
                "tier_counts": dict(sorted(source["tier_counts"].items())),
                "avg_trust_score": (
                    source["trust_total"] / trust_count if trust_count else 0.0
                ),
            }
        )

    return {
        "sources": sources,
        "total_servers": total_servers,
    }


if __name__ == "__main__":
    import asyncio

    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    registry_source TEXT,
                    risk_tier TEXT,
                    trust_score REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (registry_source, risk_tier, trust_score)
                VALUES
                    ('alpha', 'low', 0.8),
                    ('alpha', 'medium', 0.6),
                    ('beta', 'low', 0.9),
                    ('beta', 'critical', 0.4)
                """
            )
        )

    testing_session = sessionmaker(bind=engine)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    local_app = FastAPI()

    @local_app.get("/api/registry/source-mix")
    def source_mix(session: Session = Depends(get_session)) -> dict:
        return compute_source_mix(fetch_source_mix_rows(session))

    local_app.dependency_overrides[get_session] = override_get_session

    async def request_status(app: FastAPI, path: str) -> int:
        status = 0

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("127.0.0.1", 80),
        }
        await app(scope, receive, send)
        return status

    status = asyncio.run(
        request_status(local_app, "/api/registry/source-mix")
    )
    assert status == 200

    result = compute_source_mix(fetch_source_mix_rows(testing_session()))
    assert result["total_servers"] == 4
    assert {source["registry_source"] for source in result["sources"]} == {
        "alpha",
        "beta",
    }
    assert all(
        sum(source["tier_counts"].values()) == source["server_count"]
        for source in result["sources"]
    )

    print("PASS")