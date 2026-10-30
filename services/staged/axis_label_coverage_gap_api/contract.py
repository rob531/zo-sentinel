from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


CANONICAL_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]
CANONICAL_AXIS_SET = set(CANONICAL_AXES)


class ServerAxisCoverage(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    present_axes: List[str]
    missing_axes: List[str]
    present_count: int
    latest_scored_at: Optional[datetime] = None


class AxisLabelCoverageResponse(BaseModel):
    total_servers: int
    fully_scored: int
    partial: int
    servers: List[ServerAxisCoverage]


def _axis_label_coverage(
    session: Session,
    server_id: Optional[str] = None,
    min_axes: int = 1,
) -> AxisLabelCoverageResponse:
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.scored_at,
        )
        .outerjoin(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .order_by(McpServerRegistry.server_id)
    )
    if server_id is not None:
        statement = statement.where(McpServerRegistry.server_id == server_id)

    grouped = {}
    for row in session.execute(statement):
        registry_id = row.server_id
        if registry_id not in grouped:
            grouped[registry_id] = {
                "server_id": registry_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
                "axes": set(),
                "latest_scored_at": None,
            }

        entry = grouped[registry_id]
        if row.axis_name in CANONICAL_AXIS_SET:
            entry["axes"].add(row.axis_name)
        if row.scored_at is not None:
            latest = entry["latest_scored_at"]
            if latest is None or row.scored_at > latest:
                entry["latest_scored_at"] = row.scored_at

    servers = []
    for entry in grouped.values():
        present = [axis for axis in CANONICAL_AXES if axis in entry["axes"]]
        if len(present) < min_axes:
            continue
        missing = [axis for axis in CANONICAL_AXES if axis not in entry["axes"]]
        servers.append(
            ServerAxisCoverage(
                server_id=entry["server_id"],
                name=entry["name"],
                risk_tier=entry["risk_tier"],
                present_axes=present,
                missing_axes=missing,
                present_count=len(present),
                latest_scored_at=entry["latest_scored_at"],
            )
        )

    fully_scored = sum(server.present_count == len(CANONICAL_AXES) for server in servers)
    return AxisLabelCoverageResponse(
        total_servers=len(servers),
        fully_scored=fully_scored,
        partial=len(servers) - fully_scored,
        servers=servers,
    )


def create_app() -> FastAPI:
    application = FastAPI(title="axis-label-coverage-api")

    @application.get(
        "/api/axis-label-coverage",
        response_model=AxisLabelCoverageResponse,
    )
    def axis_label_coverage(
        server_id: Optional[str] = Query(default=None),
        min_axes: int = Query(default=1, ge=1, le=7),
        session: Session = Depends(get_session),
    ) -> AxisLabelCoverageResponse:
        return _axis_label_coverage(session, server_id=server_id, min_axes=min_axes)

    return application


app = create_app()


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    label VARCHAR,
                    p_top FLOAT,
                    scored_at DATETIME
                )
                """
            )
        )

    session = TestingSession()
    try:
        session.execute(
            text(
                "INSERT INTO mcp_server_registry (server_id, name, risk_tier) "
                "VALUES (:server_id, :name, :risk_tier)"
            ),
            [
                {"server_id": "server-all", "name": "All axes", "risk_tier": "high"},
                {"server_id": "server-five", "name": "Five axes", "risk_tier": "medium"},
                {"server_id": "server-one", "name": "One axis", "risk_tier": "low"},
            ],
        )

        rows = []
        scored_at = datetime(2025, 1, 1, 12, 0, 0)
        for server_id, axes in (
            ("server-all", CANONICAL_AXES),
            ("server-five", CANONICAL_AXES[:5]),
            ("server-one", CANONICAL_AXES[:1]),
        ):
            for axis_name in axes:
                rows.append(
                    {
                        "server_id": server_id,
                        "axis_name": axis_name,
                        "label": "label",
                        "p_top": 0.5,
                        "scored_at": scored_at,
                    }
                )
        session.execute(
            text(
                "INSERT INTO mcp_llm_axis_scores "
                "(server_id, axis_name, label, p_top, scored_at) "
                "VALUES (:server_id, :axis_name, :label, :p_top, :scored_at)"
            ),
            rows,
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        override_session = TestingSession()
        try:
            yield override_session
        finally:
            override_session.close()

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/axis-label-coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 3
        assert payload["fully_scored"] == 1

        by_server = {server["server_id"]: server for server in payload["servers"]}
        assert len(by_server["server-five"]["missing_axes"]) == 2
        assert len(by_server["server-one"]["missing_axes"]) == 6

    engine.dispose()


def run_contract() -> None:
    _self_test()
    print("PASS")


def run_self_test() -> None:
    _self_test()
    print("PASS")


if __name__ == "__main__":
    run_self_test()