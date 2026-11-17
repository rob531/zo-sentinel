from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import Float, case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_registry_source_trust_coverage(
    db: Session,
    source: str | None = None,
    min_trust: float | None = None,
) -> dict[str, Any]:
    conditions = []
    if source is not None:
        conditions.append(McpServerRegistry.registry_source == source)
    if min_trust is not None:
        conditions.append(McpServerRegistry.trust_score >= min_trust)

    scored_count = func.count(McpServerRegistry.trust_score)
    insufficient_count = func.sum(
        case(
            (McpServerRegistry.verdict == "INSUFFICIENT", 1),
            else_=0,
        )
    )
    null_risk_tier_count = func.sum(
        case(
            (McpServerRegistry.risk_tier.is_(None), 1),
            else_=0,
        )
    )

    statement = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("servers"),
            scored_count.label("scored"),
            func.avg(McpServerRegistry.trust_score)
            .cast(Float)
            .label("avg_trust_score"),
            func.min(McpServerRegistry.trust_score)
            .cast(Float)
            .label("min_trust_score"),
            func.max(McpServerRegistry.trust_score)
            .cast(Float)
            .label("max_trust_score"),
            insufficient_count.label("insufficient_verdicts"),
            null_risk_tier_count.label("null_risk_tier"),
        )
        .where(*conditions)
        .group_by(McpServerRegistry.registry_source)
        .order_by(
            func.count().desc(),
            McpServerRegistry.registry_source.asc(),
        )
    )

    rows = db.execute(statement).mappings().all()
    sources = []
    for row in rows:
        servers = int(row["servers"])
        scored = int(row["scored"])
        sources.append(
            {
                "registry_source": row["registry_source"],
                "servers": servers,
                "scored": scored,
                "unscored": servers - scored,
                "avg_trust_score": row["avg_trust_score"],
                "min_trust_score": row["min_trust_score"],
                "max_trust_score": row["max_trust_score"],
                "insufficient_verdicts": int(row["insufficient_verdicts"] or 0),
                "null_risk_tier": int(row["null_risk_tier"] or 0),
            }
        )

    return {
        "total": sum(item["servers"] for item in sources),
        "sources": sources,
    }


def registry_source_trust_coverage_endpoint(
    source: str | None = None,
    min_trust: float | None = None,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_registry_source_trust_coverage(
        db,
        source=source,
        min_trust=min_trust,
    )


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR(255) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    registry_source VARCHAR(255),
                    trust_score FLOAT,
                    verdict VARCHAR(64),
                    risk_tier VARCHAR(64),
                    last_assessed DATETIME,
                    first_seen DATETIME,
                    last_seen DATETIME,
                    scan_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, registry_source, trust_score,
                    verdict, risk_tier, scan_count
                ) VALUES
                    ('srv-1', 'Server 1', 'alpha', 0.8, 'SUFFICIENT', 'LOW', 3),
                    ('srv-2', 'Server 2', 'alpha', 0.6, 'INSUFFICIENT', 'MEDIUM', 2),
                    ('srv-3', 'Server 3', 'alpha', 0.9, 'SUFFICIENT', 'LOW', 5),
                    ('srv-4', 'Server 4', 'alpha', 0.7, 'SUFFICIENT', NULL, 1),
                    ('srv-5', 'Server 5', 'beta', 0.5, 'SUFFICIENT', 'LOW', 4),
                    ('srv-6', 'Server 6', 'beta', 0.4, 'SUFFICIENT', 'MEDIUM', 2),
                    ('srv-7', 'Server 7', 'beta', NULL, 'SUFFICIENT', 'LOW', 1),
                    ('srv-8', 'Server 8', 'gamma', NULL, 'SUFFICIENT', 'LOW', 1)
                """
            )
        )

    test_app = FastAPI()

    @test_app.get("/api/registry/sources/trust-coverage")
    def trust_coverage_endpoint(
        source: str | None = None,
        min_trust: float | None = None,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return registry_source_trust_coverage_endpoint(
            source=source,
            min_trust=min_trust,
            db=db,
        )

    def override_get_session():
        with testing_session() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/sources/trust-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 8
        assert len(payload["sources"]) == 3

        largest = max(payload["sources"], key=lambda item: item["servers"])
        assert largest["registry_source"] == "alpha"
        assert largest["servers"] == 4
        assert isinstance(largest["avg_trust_score"], float)
        assert abs(largest["avg_trust_score"] - 0.75) < 1e-9
        assert largest["scored"] == 4
        assert largest["unscored"] == 0
        assert largest["insufficient_verdicts"] == 1
        assert largest["null_risk_tier"] == 1

        gamma = next(
            item for item in payload["sources"] if item["registry_source"] == "gamma"
        )
        assert gamma["avg_trust_score"] is None
        assert gamma["min_trust_score"] is None
        assert gamma["max_trust_score"] is None
        assert gamma["scored"] == 0
        assert gamma["unscored"] == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()