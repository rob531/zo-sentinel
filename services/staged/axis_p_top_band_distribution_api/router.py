from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore

try:
    from .logic import get_axis_p_top_bands as _get_axis_p_top_bands
except ImportError:
    try:
        from .logic import get_axis_p_top_band_distribution as _get_axis_p_top_bands
    except ImportError:
        try:
            from .logic import get_p_top_band_distribution as _get_axis_p_top_bands
        except ImportError:
            from .logic import get_p_top_bands as _get_axis_p_top_bands


router = APIRouter(prefix="/api", tags=["axis-p-top-bands"])


class BandBoundary(BaseModel):
    band: str
    lower: float
    upper: float


class ConfidenceBand(BaseModel):
    band: str
    lower: float
    upper: float
    count: int = Field(ge=0)


class AxisBandDistribution(BaseModel):
    axis_name: str
    row_count: int = Field(ge=0)
    mean_p_top: float
    median_p_top: float
    bands: list[ConfidenceBand]


class AxisPTopBandsResponse(BaseModel):
    total_rows: int = Field(ge=0)
    axes: list[AxisBandDistribution]
    band_boundaries: list[BandBoundary]


def _response_payload(result: Any) -> Any:
    if isinstance(result, dict):
        return result
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    return result


@router.get(
    "/axis/p-top-bands",
    response_model=AxisPTopBandsResponse,
    summary="Distribution of top-class confidence bands by risk axis",
)
def get_axis_p_top_bands_route(
    axis_name: str | None = Query(
        default=None,
        max_length=200,
        description="Optionally restrict results to one risk axis.",
    ),
    limit: int = Query(
        default=1000,
        ge=1,
        le=10000,
        description="Maximum number of score rows considered per axis.",
    ),
    db: Session = Depends(get_session),
) -> AxisPTopBandsResponse:
    result = _get_axis_p_top_bands(db, axis_name, limit)
    payload = _response_payload(result)
    if isinstance(payload, dict):
        return AxisPTopBandsResponse.model_validate(payload)
    return AxisPTopBandsResponse.parse_obj(payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    seeded_rows = [
        ("alpha", 0.05),
        ("alpha", 0.35),
        ("alpha", 0.55),
        ("beta", 0.75),
        ("beta", 0.95),
    ]

    with TestSession() as session:
        for index, (axis, p_top) in enumerate(seeded_rows, start=1):
            session.add(
                McpLlmAxisScore(
                    id=index,
                    server_id=index,
                    axis_name=axis,
                    label="test-label",
                    label_index=0,
                    probs=[1.0 - p_top, p_top],
                    p_top=p_top,
                    p_critical=0.01,
                    p_danger=0.01,
                    escalated=False,
                    escalated_to=None,
                    decision_rule_version="test-rule-v1",
                    model_version="test-model-v1",
                    adapter_sha256="test-adapter-sha256",
                    scored_at=datetime.now(timezone.utc),
                )
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/p-top-bands")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_rows"] == 5
        assert len(body["axes"]) == 2

        axes = {axis["axis_name"]: axis for axis in body["axes"]}
        alpha = axes["alpha"]
        beta = axes["beta"]

        assert alpha["row_count"] == 3
        assert abs(alpha["mean_p_top"] - (0.05 + 0.35 + 0.55) / 3) < 1e-12
        assert abs(beta["mean_p_top"] - (0.75 + 0.95) / 2) < 1e-12
        assert sum(band["count"] for band in alpha["bands"]) == alpha["row_count"]
        assert sum(band["count"] for band in beta["bands"]) == beta["row_count"]

    print("PASS")