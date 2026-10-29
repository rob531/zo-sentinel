from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import Float, case, cast, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def get_verdict_reasoning_gap(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    source_rows = (
        db.query(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("servers"),
            func.sum(
                case((McpServerRegistry.verdict_reasoning.is_(None), 1), else_=0)
            ).label("missing_reasoning"),
            func.sum(
                case(
                    (
                        McpServerRegistry.verdict_reasoning.is_not(None)
                        & (func.trim(McpServerRegistry.verdict_reasoning) == ""),
                        1,
                    ),
                    else_=0,
                )
            ).label("blank_reasoning"),
            func.sum(
                case((McpServerRegistry.confidence.is_(None), 1), else_=0)
            ).label("missing_confidence"),
            func.avg(cast(McpServerRegistry.confidence, Float)).label(
                "mean_confidence"
            ),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
        .all()
    )

    sources: List[Dict[str, Any]] = []
    for row in source_rows:
        sources.append(
            {
                "registry_source": row.registry_source,
                "servers": int(row.servers),
                "missing_reasoning": int(row.missing_reasoning or 0),
                "blank_reasoning": int(row.blank_reasoning or 0),
                "missing_confidence": int(row.missing_confidence or 0),
                "mean_confidence": (
                    float(row.mean_confidence)
                    if row.mean_confidence is not None
                    else None
                ),
            }
        )

    totals_row = (
        db.query(
            func.count().label("servers"),
            func.sum(
                case((McpServerRegistry.verdict_reasoning.is_(None), 1), else_=0)
            ).label("missing_reasoning"),
            func.sum(
                case((McpServerRegistry.confidence.is_(None), 1), else_=0)
            ).label("missing_confidence"),
            func.avg(cast(McpServerRegistry.confidence, Float)).label(
                "mean_confidence"
            ),
        ).one()
    )

    totals: Dict[str, Any] = {
        "servers": int(totals_row.servers),
        "missing_reasoning": int(totals_row.missing_reasoning or 0),
        "missing_confidence": int(totals_row.missing_confidence or 0),
        "mean_confidence": (
            float(totals_row.mean_confidence)
            if totals_row.mean_confidence is not None
            else None
        ),
    }

    return {"sources": sources, "totals": totals}


get_registry_verdict_reasoning_gap = get_verdict_reasoning_gap


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    registry_source VARCHAR,
                    verdict VARCHAR,
                    verdict_reasoning TEXT,
                    confidence FLOAT,
                    last_assessed DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    registry_source,
                    verdict,
                    verdict_reasoning,
                    confidence,
                    last_assessed
                ) VALUES
                    ('server-1', 'alpha', 'malicious', NULL, 0.90, CURRENT_TIMESTAMP),
                    ('server-2', 'alpha', 'safe', '', 0.80, CURRENT_TIMESTAMP),
                    ('server-3', 'beta', 'safe', 'Reviewed', NULL, CURRENT_TIMESTAMP),
                    ('server-4', 'beta', 'safe', 'Reviewed', 0.60, CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/verdict-reasoning-gap")
    def verdict_reasoning_gap_endpoint(
        session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_verdict_reasoning_gap(session)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-reasoning-gap")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["servers"] == 4
        assert payload["totals"]["missing_reasoning"] == 1
        assert payload["totals"]["missing_confidence"] == 1
        assert sum(row["missing_reasoning"] for row in payload["sources"]) == 1
        assert sum(row["blank_reasoning"] for row in payload["sources"]) == 1

    print("PASS")