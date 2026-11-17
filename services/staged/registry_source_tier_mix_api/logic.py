from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SourceMix(BaseModel):
    registry_source: str
    total: int
    tiers: dict[str, int]


class SourceTierMixResponse(BaseModel):
    sources: list[SourceMix]
    totals: dict[str, int]


def get_source_tier_mix(
    session: Session = Depends(get_session),
    registry_source: str | None = None,
    risk_tier: str | None = None,
) -> dict[str, Any]:
    filters: list[str] = []
    params: dict[str, Any] = {}

    if registry_source is not None:
        filters.append("registry_source = :registry_source")
        params["registry_source"] = registry_source

    if risk_tier is not None:
        filters.append("risk_tier = :risk_tier")
        params["risk_tier"] = risk_tier

    where_clause = ""
    if filters:
        where_clause = " WHERE " + " AND ".join(filters)

    statement = text(
        f"""
        SELECT registry_source, risk_tier, COUNT(*) AS count
        FROM {McpServerRegistry.__tablename__}
        {where_clause}
        GROUP BY registry_source, risk_tier
        ORDER BY registry_source, risk_tier
        """
    )

    source_tiers: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}

    for row in session.execute(statement, params):
        source = row[0]
        tier = row[1]
        count = int(row[2])

        if source is None or tier is None:
            continue

        source_name = str(source)
        tier_name = str(tier)
        source_tiers.setdefault(source_name, {})[tier_name] = count
        totals[tier_name] = totals.get(tier_name, 0) + count

    sources = [
        SourceMix(
            registry_source=source,
            total=sum(tiers.values()),
            tiers=tiers,
        )
        for source, tiers in sorted(source_tiers.items())
    ]

    return {
        "sources": [source.model_dump() for source in sources],
        "totals": {tier: totals[tier] for tier in sorted(totals)},
    }


compute_source_tier_mix = get_source_tier_mix


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    risk_tier VARCHAR
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
                    (:server_id, :name, :registry_source, :risk_tier)
                """
            ),
            [
                {
                    "server_id": "server-1",
                    "name": "Server One",
                    "registry_source": "source-a",
                    "risk_tier": "high",
                },
                {
                    "server_id": "server-2",
                    "name": "Server Two",
                    "registry_source": "source-a",
                    "risk_tier": "medium",
                },
                {
                    "server_id": "server-3",
                    "name": "Server Three",
                    "registry_source": "source-b",
                    "risk_tier": "high",
                },
            ],
        )

    def override_get_session():
        test_session = TestSession()
        try:
            yield test_session
        finally:
            test_session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get(
        "/api/registry/source-tier-mix",
        response_model=SourceTierMixResponse,
    )
    def source_tier_mix_endpoint(payload: dict[str, Any] = Depends(get_source_tier_mix)):
        return payload

    response = TestClient(test_app).get("/api/registry/source-tier-mix")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 2

    source_counts = {
        source["registry_source"]: source for source in payload["sources"]
    }
    assert source_counts["source-a"]["total"] == 2
    assert source_counts["source-a"]["tiers"] == {"high": 1, "medium": 1}
    assert source_counts["source-b"]["total"] == 1
    assert source_counts["source-b"]["tiers"] == {"high": 1}
    assert sum(payload["totals"].values()) == 3

    print("PASS")