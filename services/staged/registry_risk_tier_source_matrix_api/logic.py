from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, literal, or_, select

from app.db import get_session
from app.models import McpServerRegistry


async def _execute(conn: Any, statement: Any) -> Any:
    result = conn.execute(statement)
    if inspect.isawaitable(result):
        result = await result
    return result


async def compute_source_tier_matrix(conn: Any) -> list[dict]:
    """Return risk-tier counts and registry statistics grouped by source."""
    registry = McpServerRegistry.__table__.c
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    source_expr = func.coalesce(
        registry.registry_source,
        literal("unknown"),
    ).label("registry_source")
    tier_expr = func.coalesce(
        registry.risk_tier,
        literal("unknown"),
    ).label("risk_tier")
    freshness_expr = func.coalesce(
        registry.last_assessed,
        registry.last_seen,
        registry.first_seen,
    )
    stale_expr = case(
        (
            or_(
                freshness_expr.is_(None),
                freshness_expr < cutoff,
            ),
            1,
        ),
        else_=0,
    )

    source_statement = (
        select(
            source_expr,
            func.count().label("total_servers"),
            func.avg(registry.trust_score).label("mean_trust_score"),
            func.sum(stale_expr).label("stale_over_30d"),
        )
        .group_by(source_expr)
        .order_by(source_expr)
    )
    tier_statement = (
        select(
            source_expr,
            tier_expr,
            func.count().label("server_count"),
        )
        .group_by(source_expr, tier_expr)
        .order_by(source_expr, tier_expr)
    )

    source_result = await _execute(conn, source_statement)
    tier_result = await _execute(conn, tier_statement)

    sources: dict[str, dict] = {}

    for row in source_result.mappings().all():
        registry_source = str(row["registry_source"])
        mean_trust_score = row["mean_trust_score"]
        sources[registry_source] = {
            "registry_source": registry_source,
            "total_servers": int(row["total_servers"]),
            "tiers": {},
            "mean_trust_score": (
                float(mean_trust_score)
                if mean_trust_score is not None
                else None
            ),
            "stale_over_30d": int(row["stale_over_30d"] or 0),
        }

    for row in tier_result.mappings().all():
        registry_source = str(row["registry_source"])
        risk_tier = str(row["risk_tier"])
        source = sources.setdefault(
            registry_source,
            {
                "registry_source": registry_source,
                "total_servers": 0,
                "tiers": {},
                "mean_trust_score": None,
                "stale_over_30d": 0,
            },
        )
        source["tiers"][risk_tier] = int(row["server_count"])

    for source in sources.values():
        source["total_servers"] = sum(source["tiers"].values())

    return [sources[registry_source] for registry_source in sorted(sources)]


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc)
    rows = [
        McpServerRegistry(
            server_id="matrix-1",
            name="matrix-1",
            url="https://example.invalid/matrix-1",
            registry_source="registry-a",
            risk_tier="HIGH_RISK_ISOLATED",
            trust_score=0.9,
            first_seen=now,
            last_seen=now,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="matrix-2",
            name="matrix-2",
            url="https://example.invalid/matrix-2",
            registry_source="registry-a",
            risk_tier="HIGH_RISK_ISOLATED",
            trust_score=0.8,
            first_seen=now,
            last_seen=now,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="matrix-3",
            name="matrix-3",
            url="https://example.invalid/matrix-3",
            registry_source="registry-b",
            risk_tier="LOW",
            trust_score=None,
            first_seen=now,
            last_seen=now,
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="matrix-4",
            name="matrix-4",
            url="https://example.invalid/matrix-4",
            registry_source="registry-b",
            risk_tier=None,
            trust_score=None,
            first_seen=now,
            last_seen=now,
            last_assessed=now,
        ),
    ]

    with Session(engine) as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        session = Session(engine)
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/risk-tier-source-matrix")
    async def registry_risk_tier_source_matrix(
        min_servers: int = 0,
        conn: Any = Depends(get_session),
    ) -> dict:
        all_sources = await compute_source_tier_matrix(conn)
        sources = [
            source
            for source in all_sources
            if source["total_servers"] >= min_servers
        ]
        totals: dict[str, int] = {}
        for source in sources:
            for risk_tier, count in source["tiers"].items():
                totals[risk_tier] = totals.get(risk_tier, 0) + count
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": sources,
            "totals": totals,
            "registry_count": len(sources),
        }

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/risk-tier-source-matrix"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["registry_count"] == 2
        assert body["totals"]["unknown"] == 1
        registry_b = next(
            source
            for source in body["sources"]
            if source["registry_source"] == "registry-b"
        )
        assert registry_b["mean_trust_score"] is None

    print("PASS")


if __name__ == "__main__":
    _self_test()