from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api", tags=["registry"])


class NeverAssessedServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    scan_count: Optional[int] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    has_axis_scores: bool


class NeverAssessedResponse(BaseModel):
    total_never_assessed: int
    never_assessed_without_scores: int
    limit: int
    offset: int
    servers: list[NeverAssessedServer]


@router.get(
    "/registry/never-assessed",
    response_model=NeverAssessedResponse,
    summary="List registered MCP servers that have never been assessed",
)
def list_never_assessed(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> NeverAssessedResponse:
    total_never_assessed = int(
        session.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.last_assessed.is_(None))
        ).scalar_one()
    )

    unscored_servers = (
        select(McpServerRegistry.server_id)
        .select_from(McpServerRegistry)
        .outerjoin(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(
            McpServerRegistry.last_assessed.is_(None),
            McpLlmAxisScore.id.is_(None),
        )
        .subquery()
    )

    never_assessed_without_scores = int(
        session.execute(
            select(func.count()).select_from(unscored_servers)
        ).scalar_one()
    )

    registry_columns = (
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.first_seen,
        McpServerRegistry.last_seen,
        McpServerRegistry.scan_count,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
    )

    rows = session.execute(
        select(
            *registry_columns,
            func.count(McpLlmAxisScore.id).label("axis_score_count"),
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(McpServerRegistry.last_assessed.is_(None))
        .group_by(*registry_columns)
        .order_by(
            McpServerRegistry.first_seen.asc(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()

    servers = [
        NeverAssessedServer(
            server_id=row.server_id,
            name=row.name,
            registry_source=row.registry_source,
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            scan_count=row.scan_count,
            risk_tier=row.risk_tier,
            verdict=row.verdict,
            has_axis_scores=row.axis_score_count > 0,
        )
        for row in rows
    ]

    return NeverAssessedResponse(
        total_never_assessed=total_never_assessed,
        never_assessed_without_scores=never_assessed_without_scores,
        limit=limit,
        offset=offset,
        servers=servers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    first_seen DATETIME,
                    last_assessed DATETIME,
                    last_seen DATETIME,
                    scan_count INTEGER,
                    risk_tier VARCHAR,
                    verdict VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, registry_source, first_seen,
                    last_assessed, last_seen, scan_count, risk_tier, verdict
                ) VALUES
                    (
                        'never-unscored', 'Never Unscored', 'test',
                        CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, 0,
                        'unknown', NULL
                    ),
                    (
                        'never-scored', 'Never Scored', 'test',
                        CURRENT_TIMESTAMP, NULL, CURRENT_TIMESTAMP, 1,
                        'unknown', NULL
                    ),
                    (
                        'assessed-one', 'Assessed One', 'test',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                        2, 'low', 'approved'
                    ),
                    (
                        'assessed-two', 'Assessed Two', 'test',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,
                        3, 'medium', 'review'
                    )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    server_id, axis_name, scored_at
                ) VALUES (
                    'never-scored', 'security', CURRENT_TIMESTAMP
                )
                """
            )
        )

    test_session = TestSession()

    def override_get_session():
        yield test_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/registry/never-assessed",
                params={"limit": 100, "offset": 0},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_never_assessed"] == 2, payload
            assert payload["never_assessed_without_scores"] == 1, payload
            assert payload["limit"] == 100
            assert payload["offset"] == 0

            servers_by_id = {
                server["server_id"]: server for server in payload["servers"]
            }
            assert "never-unscored" in servers_by_id, payload
            assert servers_by_id["never-unscored"]["has_axis_scores"] is False
            assert servers_by_id["never-scored"]["has_axis_scores"] is True

        print("PASS")
    finally:
        test_session.close()
        test_engine.dispose()