"""router.py -- the service's HTTP surface for axis p-top histograms.

Exposes `router` for spine mounting. The route is read-only and delegates
axis validation and aggregation to the service logic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import axis_p_top_histogram

router = APIRouter(prefix="/api", tags=["axis"])

ALLOWED_AXES = frozenset(
    {
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }
)


class HistogramBin(BaseModel):
    index: int
    lower: float
    upper: float
    count: int


class AxisHistogramResponse(BaseModel):
    axis_name: str
    bins: list[HistogramBin]
    server_count: int
    critical_count: int
    escalated_count: int
    mean_p_top: float


@router.get("/axis/histogram", response_model=AxisHistogramResponse)
def get_axis_histogram(
    axis_name: str,
    bins: int = 10,
    db: Session = Depends(get_session),
) -> AxisHistogramResponse | JSONResponse:
    if axis_name not in ALLOWED_AXES:
        return JSONResponse(status_code=400, content={"error": "unknown_axis"})

    if bins <= 0:
        return JSONResponse(status_code=400, content={"error": "invalid_bins"})

    result = axis_p_top_histogram(db, axis_name=axis_name, bins=bins)
    return AxisHistogramResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: None

    with TestClient(that_app) as client:
        response = client.get(
            "/api/axis/histogram",
            params={"axis_name": "overall_risk"},
        )
        assert response.status_code == 400
        assert response.json() == {"error": "unknown_axis"}

    print("PASS")