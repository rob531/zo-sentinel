from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class TopCriticalServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    registry_source: Optional[str] = None
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_critical: float
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    scored_at: Optional[datetime] = None


class TopCriticalServersResponse(BaseModel):
    axis: str
    limit: int
    servers: list[TopCriticalServer]


def get_top_critical_servers(
    session: Session,
    axis: str,
    limit: int,
) -> list[dict[str, Any]]:
    query = text(
        """
        SELECT
            scores.server_id,
            registry.name,
            registry.verdict,
            registry.risk_tier,
            registry.registry_source,
            scores.label,
            scores.label_index,
            scores.p_critical,
            scores.p_danger,
            scores.escalated,
            scores.escalated_to,
            scores.scored_at
        FROM mcp_llm_axis_scores AS scores
        INNER JOIN mcp_server_registry AS registry
            ON registry.server_id = scores.server_id
        WHERE scores.axis_name = :axis
        ORDER BY scores.p_critical DESC, scores.server_id ASC
        LIMIT :limit
        """
    )
    result = session.execute(query, {"axis": axis, "limit": limit})
    return [dict(row) for row in result.mappings().all()]


@router.get(
    "/axis/top-critical-servers",
    response_model=TopCriticalServersResponse,
)
def top_critical_servers_endpoint(
    axis: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    servers = get_top_critical_servers(session, axis, limit)
    return {"axis": axis, "limit": limit, "servers": servers}


def main() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    with TestingSessionLocal() as session:
        session.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    verdict VARCHAR,
                    risk_tier VARCHAR,
                    registry_source VARCHAR
                )
                """
            )
        )
        session.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    label VARCHAR,
                    label_index INTEGER,
                    p_top FLOAT,
                    p_critical FLOAT NOT NULL,
                    p_danger FLOAT,
                    escalated BOOLEAN NOT NULL DEFAULT 0,
                    escalated_to VARCHAR,
                    scored_at TIMESTAMP
                )
                """
            )
        )

        session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, verdict, risk_tier, registry_source)
                VALUES
                    (:server_id, :name, :verdict, :risk_tier, :registry_source)
                """
            ),
            [
                {
                    "server_id": "server-critical",
                    "name": "Critical Server",
                    "verdict": "block",
                    "risk_tier": "critical",
                    "registry_source": "internal",
                },
                {
                    "server_id": "server-high",
                    "name": "High Server",
                    "verdict": "review",
                    "risk_tier": "high",
                    "registry_source": "internal",
                },
                {
                    "server_id": "server-low",
                    "name": "Low Server",
                    "verdict": "allow",
                    "risk_tier": "low",
                    "registry_source": "internal",
                },
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, label, label_index, p_top,
                     p_critical, p_danger, escalated, escalated_to, scored_at)
                VALUES
                    (:server_id, :axis_name, :label, :label_index, :p_top,
                     :p_critical, :p_danger, :escalated, :escalated_to, :scored_at)
                """
            ),
            [
                {
                    "server_id": "server-critical",
                    "axis_name": "exploit_surface",
                    "label": "critical",
                    "label_index": 0,
                    "p_top": 0.95,
                    "p_critical": 0.9,
                    "p_danger": 0.85,
                    "escalated": True,
                    "escalated_to": "security_team",
                    "scored_at": "2025-01-01T00:00:00",
                },
                {
                    "server_id": "server-high",
                    "axis_name": "exploit_surface",
                    "label": "high",
                    "label_index": 1,
                    "p_top": 0.65,
                    "p_critical": 0.4,
                    "p_danger": 0.5,
                    "escalated": False,
                    "escalated_to": None,
                    "scored_at": "2025-01-01T00:00:01",
                },
                {
                    "server_id": "server-low",
                    "axis_name": "exploit_surface",
                    "label": "low",
                    "label_index": 2,
                    "p_top": 0.2,
                    "p_critical": 0.1,
                    "p_danger": 0.15,
                    "escalated": False,
                    "escalated_to": None,
                    "scored_at": "2025-01-01T00:00:02",
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/axis/top-critical-servers",
                params={"axis": "exploit_surface", "limit": 20},
            )

            assert response.status_code == 200
            payload = response.json()
            assert len(payload["servers"]) == 3
            assert payload["servers"][0]["server_id"] == "server-critical"
            assert payload["servers"][0]["escalated_to"] == "security_team"
    finally:
        that_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()