"""HTTP surface for MCP server axis-confidence scoring."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_confidence

router = APIRouter(prefix="/api/axis", tags=["axis-confidence"])


class AxisConfidenceParams(BaseModel):
    axis_name: str | None = None
    limit: int = Field(default=20, ge=1, le=200)


class AxisAggregate(BaseModel):
    scored_count: int
    mean_p_top: float | None
    mean_p_critical: float | None
    mean_p_danger: float | None
    escalated_count: int
    low_confidence_count: int


class LowConfidenceServer(BaseModel):
    server_id: str
    name: str | None
    risk_tier: str | None
    weakest_axis: str
    confidence: float


class AxisConfidenceResponse(BaseModel):
    axis_aggregates: dict[str, AxisAggregate]
    lowest_confidence_servers: list[LowConfidenceServer]


def _query_params(
    axis_name: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
) -> AxisConfidenceParams:
    return AxisConfidenceParams(axis_name=axis_name, limit=limit)


def _response_parts(payload: Any) -> tuple[Any, Any]:
    if isinstance(payload, tuple):
        if len(payload) != 2:
            raise ValueError("axis-confidence logic returned an invalid payload")
        return payload

    if isinstance(payload, dict):
        aggregate_keys = ("axis_aggregates", "aggregates", "axes", "axis_scores")
        server_keys = (
            "lowest_confidence_servers",
            "low_confidence_servers",
            "servers",
            "lowest_confidence",
        )
        aggregates = next(
            (payload[key] for key in aggregate_keys if key in payload),
            None,
        )
        servers = next((payload[key] for key in server_keys if key in payload), None)
        if aggregates is not None and servers is not None:
            return aggregates, servers

    raise ValueError("axis-confidence logic returned an invalid payload")


@router.get("/confidence", response_model=AxisConfidenceResponse)
def get_axis_confidence_endpoint(
    params: AxisConfidenceParams = Depends(_query_params),
    db: Session = Depends(get_session),
) -> AxisConfidenceResponse:
    payload = get_axis_confidence(
        db,
        axis_name=params.axis_name,
        limit=params.limit,
    )
    aggregates, servers = _response_parts(payload)
    return AxisConfidenceResponse(
        axis_aggregates=aggregates,
        lowest_confidence_servers=servers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore, McpServerRegistry

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-confident",
                    name="confident-server",
                    risk_tier="low",
                    verdict="approved",
                ),
                McpServerRegistry(
                    server_id="server-low-confidence",
                    name="low-confidence-server",
                    risk_tier="high",
                    verdict="review",
                ),
                McpServerRegistry(
                    server_id="server-moderate",
                    name="moderate-server",
                    risk_tier="medium",
                    verdict="approved",
                ),
            ]
        )
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-confident",
                    axis_name="critical",
                    p_top=0.9,
                    p_critical=0.8,
                    p_danger=0.7,
                    escalated=True,
                    escalated_to="security",
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-low-confidence",
                    axis_name="high",
                    p_top=0.2,
                    p_critical=0.2,
                    p_danger=0.2,
                    escalated=False,
                    model_version="test-model",
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-moderate",
                    axis_name="medium",
                    p_top=0.6,
                    p_critical=0.5,
                    p_danger=0.4,
                    escalated=False,
                    model_version="test-model",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    with TestClient(test_app) as client:
        response = client.get("/api/axis/confidence")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body["axis_aggregates"]) == {"critical", "high", "medium"}
        assert body["axis_aggregates"]["critical"]["escalated_count"] == 1
        assert any(
            server["server_id"] == "server-low-confidence"
            for server in body["lowest_confidence_servers"]
        )

    print("PASS")