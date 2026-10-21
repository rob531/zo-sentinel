from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class AxisVersionDrift(BaseModel):
    model_version: Optional[str]
    decision_rule_version: Optional[str]
    adapter_sha256: Optional[str]
    server_count: int
    first_scored_at: Optional[datetime]
    last_scored_at: Optional[datetime]
    fleet_share_pct: float


class AxisModelDriftResponse(BaseModel):
    total_servers: int
    versions: List[AxisVersionDrift]
    stale_adapter_count: int


router = APIRouter(prefix="/api")


@router.get("/axes/model-drift", response_model=AxisModelDriftResponse)
def get_axis_model_drift(session: Session = Depends(get_session)) -> AxisModelDriftResponse:
    total_servers = int(
        session.execute(
            select(func.count(func.distinct(McpServerRegistry.server_id)))
        ).scalar_one()
        or 0
    )

    rows = session.execute(
        select(
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            McpLlmAxisScore.adapter_sha256.label("adapter_sha256"),
            func.count(func.distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
        )
        .order_by(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
        )
    ).all()

    versions = [
        AxisVersionDrift(
            model_version=row.model_version,
            decision_rule_version=row.decision_rule_version,
            adapter_sha256=row.adapter_sha256,
            server_count=int(row.server_count),
            first_scored_at=row.first_scored_at,
            last_scored_at=row.last_scored_at,
            fleet_share_pct=(
                (int(row.server_count) / total_servers) * 100.0
                if total_servers
                else 0.0
            ),
        )
        for row in rows
    ]

    stale_servers = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.model_version.label("model_version"),
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
        )
        .group_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
        )
        .having(func.count(func.distinct(McpLlmAxisScore.adapter_sha256)) > 1)
        .subquery()
    )
    stale_adapter_count = int(
        session.execute(
            select(func.count(func.distinct(stale_servers.c.server_id)))
        ).scalar_one()
        or 0
    )

    return AxisModelDriftResponse(
        total_servers=total_servers,
        versions=versions,
        stale_adapter_count=stale_adapter_count,
    )


app = FastAPI(title="axis_model_version_drift_api")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR NOT NULL,
                model_version VARCHAR NOT NULL,
                decision_rule_version VARCHAR NOT NULL,
                adapter_sha256 VARCHAR NOT NULL,
                scored_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_server_registry (server_id)
            VALUES ('server-a'), ('server-b'), ('server-c')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO mcp_llm_axis_scores (
                id, server_id, model_version, decision_rule_version,
                adapter_sha256, scored_at
            )
            VALUES
                (1, 'server-a', 'model-v1', 'rules-v1', 'adapter-a',
                 '2026-01-01T00:00:00'),
                (2, 'server-b', 'model-v1', 'rules-v1', 'adapter-a',
                 '2026-01-01T00:01:00'),
                (3, 'server-c', 'model-v2', 'rules-v1', 'adapter-b',
                 '2026-01-01T00:02:00'),
                (4, 'server-a', 'model-v1', 'rules-v1', 'adapter-a',
                 '2026-01-01T00:03:00')
            """
        )

    test_session_factory = sessionmaker(bind=engine, autoflush=False)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/axes/model-drift")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_servers"] == 3
            assert len(payload["versions"]) == 2
            assert any(
                version["model_version"] == "model-v1"
                and version["server_count"] == 2
                for version in payload["versions"]
            )
            assert abs(sum(version["fleet_share_pct"] for version in payload["versions"]) - 100.0) < 1e-9
            assert payload["stale_adapter_count"] == 0
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")