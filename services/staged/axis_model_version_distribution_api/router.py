from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "services.staged.axis_model_version_distribution_api"

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import get_axis_model_version_distribution

router = APIRouter(prefix="/api", tags=["axis-model-version-distribution"])


class AxisModelVersionDistributionGroup(BaseModel):
    model_version: str
    decision_rule_version: str
    adapter_sha256: str
    rows: int
    servers: int
    avg_p_top: float
    last_scored_at: datetime


class AxisModelVersionDistributionAxis(BaseModel):
    axis_name: str
    model_version: str
    rows: int
    servers: int
    avg_p_top: float


class AxisModelVersionDistributionResponse(BaseModel):
    total_scored: int
    groups: list[AxisModelVersionDistributionGroup]
    by_axis: list[AxisModelVersionDistributionAxis]


@router.get(
    "/axis/model-version-distribution",
    response_model=AxisModelVersionDistributionResponse,
)
def axis_model_version_distribution(
    session: Session = Depends(get_session),
) -> AxisModelVersionDistributionResponse:
    return get_axis_model_version_distribution(session)


def _self_test() -> None:
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
    table_name = McpLlmAxisScore.__tablename__
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adapter_sha256 TEXT,
                    axis_name TEXT NOT NULL,
                    decision_rule_version TEXT NOT NULL,
                    escalated BOOLEAN,
                    escalated_to TEXT,
                    label TEXT,
                    label_index INTEGER,
                    model_version TEXT NOT NULL,
                    p_critical FLOAT,
                    p_danger FLOAT,
                    p_top FLOAT NOT NULL,
                    probs TEXT,
                    scored_at DATETIME NOT NULL,
                    server_id TEXT NOT NULL
                )
                """
            )
        )

    axes = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )
    models = (
        ("model-v1", "rule-v1", 0.4),
        ("model-v2", "rule-v2", 0.8),
    )
    servers = ("server-1", "server-2", "server-3")
    scored_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    rows = []
    for model_version, decision_rule_version, p_top in models:
        for axis_name in axes:
            for server_id in servers:
                rows.append(
                    {
                        "adapter_sha256": f"sha-{model_version}",
                        "axis_name": axis_name,
                        "decision_rule_version": decision_rule_version,
                        "model_version": model_version,
                        "p_top": p_top,
                        "scored_at": scored_at,
                        "server_id": server_id,
                    }
                )

    insert_sql = text(
        f"""
        INSERT INTO {table_name} (
            adapter_sha256,
            axis_name,
            decision_rule_version,
            model_version,
            p_top,
            scored_at,
            server_id
        ) VALUES (
            :adapter_sha256,
            :axis_name,
            :decision_rule_version,
            :model_version,
            :p_top,
            :scored_at,
            :server_id
        )
        """
    )
    with engine.begin() as connection:
        connection.execute(insert_sql, rows)

    test_session_factory = sessionmaker(bind=engine, autoflush=False)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/axis/model-version-distribution")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_scored"] == len(rows)
        assert len(payload["groups"]) == 2
        assert any(
            group["model_version"] == "model-v1" and group["rows"] == 21
            for group in payload["groups"]
        )
        assert {entry["axis_name"] for entry in payload["by_axis"]} == set(axes)

    print("PASS")


if __name__ == "__main__":
    _self_test()