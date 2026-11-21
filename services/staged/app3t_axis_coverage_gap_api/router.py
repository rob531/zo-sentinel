"""HTTP surface for the app3t axis coverage gap service."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_axis_coverage_gaps

router = APIRouter(prefix="/api/app3t/axis", tags=["app3t", "axis", "coverage"])


class AxisCoverageGap(BaseModel):
    axis_name: str
    scored_servers: int
    missing_servers: int
    coverage_pct: float
    latest_scored_at: datetime | None


class AxisCoverageTotals(BaseModel):
    servers: int
    fully_scored: int
    any_missing: int


class AxisCoverageGapResponse(BaseModel):
    axes: list[AxisCoverageGap]
    totals: AxisCoverageTotals


@router.get("/coverage-gaps", response_model=AxisCoverageGapResponse)
def axis_coverage_gaps(db: Session = Depends(get_session)) -> AxisCoverageGapResponse:
    return get_axis_coverage_gaps(db)


if __name__ == "__main__":
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app.models import McpLlmAxisScore, McpServerRegistry

    axes = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    db = TestSession()
    try:
        server_ids = [str(uuid4()) for _ in range(3)]
        for server_id in server_ids:
            db.add(McpServerRegistry(server_id=server_id))

        for server_index, server_id in enumerate(server_ids):
            for axis_index, axis_name in enumerate(axes):
                if server_index == 2:
                    continue
                if server_index == 1 and axis_name == "exploit_surface":
                    continue
                db.add(
                    McpLlmAxisScore(
                        id=str(uuid4()),
                        server_id=server_id,
                        axis_name=axis_name,
                        model_version="self-test",
                    )
                )
        db.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            return db

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/app3t/axis/coverage-gaps")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["axes"]) == 7
            exploit_surface = next(
                item for item in payload["axes"] if item["axis_name"] == "exploit_surface"
            )
            assert exploit_surface["missing_servers"] == 2
            assert all(0 <= item["coverage_pct"] <= 100 for item in payload["axes"])

        print("PASS")
    finally:
        db.close()
        test_engine.dispose()