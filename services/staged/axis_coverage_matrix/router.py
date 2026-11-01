from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import coverage_matrix

router = APIRouter(prefix="/api", tags=["axis-coverage-matrix"])


class AxisCoverageResponse(BaseModel):
    axis_name: str
    scored_servers: int
    coverage_pct: float
    escalated: int
    mean_p_top: float
    max_p_top: float
    decision_rule_versions: list[str]


class CoverageMatrixResponse(BaseModel):
    generated_at: str
    total_servers: int
    axes: list[AxisCoverageResponse]
    servers_with_all_7_axes: int
    servers_missing_any_axis: int


@router.get(
    "/axis/coverage-matrix",
    response_model=CoverageMatrixResponse,
)
def get_coverage_matrix(
    db: Session = Depends(get_session),
) -> CoverageMatrixResponse:
    return CoverageMatrixResponse(**coverage_matrix(db))


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpLlmAxisScore, McpServerRegistry

    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    test_session = test_session_factory()

    for index, server_id in enumerate(("server-1", "server-2", "server-3"), 1):
        test_session.add(
            McpServerRegistry(
                server_id=server_id,
                name=server_id,
                risk_tier="high",
            )
        )

    score_id = 1
    for server_id in ("server-1", "server-2", "server-3"):
        server_axes = axes if server_id != "server-3" else axes[:5]
        for axis_name in server_axes:
            test_session.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id=server_id,
                    axis_name=axis_name,
                    model_version="test-model",
                    p_top=0.75,
                    escalated=server_id == "server-2" and axis_name == "overall_risk",
                    decision_rule_version="v1",
                )
            )
            score_id += 1
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session
    with TestClient(test_app) as client:
        response = client.get("/api/axis/coverage-matrix")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["axes"]) == 7
        assert isinstance(payload["axes"][0]["coverage_pct"], float)
        assert 0 <= payload["axes"][0]["coverage_pct"] <= 100
        assert payload["servers_missing_any_axis"] == 1

    test_session.close()
    engine.dispose()
    print("PASS")