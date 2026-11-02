from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

MAX_LIMIT = 500
DEFAULT_LIMIT = 50


class ServerAxisGap(BaseModel):
    server_id: str
    missing_axes: list[str]
    scored_axes: int
    last_scored_at: Optional[datetime]


class ServerAxisGapReport(BaseModel):
    servers: list[ServerAxisGap]
    total: int


router = APIRouter(prefix="/api")


@router.get("/scoring/gaps", response_model=ServerAxisGapReport)
def get_server_axis_gaps(
    limit: int = Query(DEFAULT_LIMIT),
    session: Session = Depends(get_session),
) -> ServerAxisGapReport:
    effective_limit = max(0, min(limit, MAX_LIMIT))
    canonical_axes = set(CANONICAL_AXES)

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
        )
        .outerjoin(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
    ).all()

    axes_by_server: dict[str, set[str]] = {}
    last_scored_by_server: dict[str, datetime] = {}

    for server_id, axis_name, scored_at in rows:
        if axis_name is not None:
            axes_by_server.setdefault(server_id, set()).add(axis_name)
        if scored_at is not None:
            previous = last_scored_by_server.get(server_id)
            if previous is None or scored_at > previous:
                last_scored_by_server[server_id] = scored_at

    gaps: list[ServerAxisGap] = []
    for server_id in axes_by_server:
        present_axes = axes_by_server[server_id]
        missing_axes = canonical_axes - present_axes
        if missing_axes:
            gaps.append(
                ServerAxisGap(
                    server_id=server_id,
                    missing_axes=sorted(missing_axes),
                    scored_axes=len(present_axes),
                    last_scored_at=last_scored_by_server.get(server_id),
                )
            )

    server_ids = [row[0] for row in rows]
    for server_id in server_ids:
        if server_id in axes_by_server:
            continue
        missing_axes = canonical_axes
        gaps.append(
            ServerAxisGap(
                server_id=server_id,
                missing_axes=sorted(missing_axes),
                scored_axes=0,
                last_scored_at=None,
            )
        )

    gaps.sort(key=lambda item: (-len(item.missing_axes), item.server_id))
    return ServerAxisGapReport(
        servers=gaps[:effective_limit],
        total=len(gaps),
    )


def create_app() -> FastAPI:
    application = FastAPI(title="Server Axis Gap Report")
    application.include_router(router)
    return application


app = create_app()


def run_self_test() -> None:
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
                    server_id VARCHAR PRIMARY KEY
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR NOT NULL,
                    axis_name VARCHAR NOT NULL,
                    model_version VARCHAR NOT NULL,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id)
                VALUES ('fully-scored'), ('partially-scored'), ('unscored')
                """
            )
        )

        score_id = 1
        for axis_name in CANONICAL_AXES:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (id, server_id, axis_name, model_version, scored_at)
                    VALUES
                        (:id, 'fully-scored', :axis_name, 'test-model', '2025-01-01T00:00:00')
                    """
                ),
                {"id": score_id, "axis_name": axis_name},
            )
            score_id += 1

        for axis_name in CANONICAL_AXES[:3]:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores
                        (id, server_id, axis_name, model_version, scored_at)
                    VALUES
                        (:id, 'partially-scored', :axis_name, 'test-model', '2025-01-02T00:00:00')
                    """
                ),
                {"id": score_id, "axis_name": axis_name},
            )
            score_id += 1

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/scoring/gaps")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2

            servers = payload["servers"]
            server_ids = {server["server_id"] for server in servers}
            assert "fully-scored" not in server_ids
            assert "partially-scored" in server_ids
            assert "unscored" in server_ids

            partial = next(
                server for server in servers if server["server_id"] == "partially-scored"
            )
            unscored = next(
                server for server in servers if server["server_id"] == "unscored"
            )
            assert len(partial["missing_axes"]) == 4
            assert len(unscored["missing_axes"]) == 7
    finally:
        engine.dispose()

    print("PASS")


main = run_self_test


if __name__ == "__main__":
    run_self_test()