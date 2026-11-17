from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_label_coverage


class LabelCoverage(BaseModel):
    label: str
    label_index: int
    count: int


class AxisLabelCoverage(BaseModel):
    axis_name: str
    rows: int
    labels: list[LabelCoverage]
    missing_label_indexes: list[int]


class AxisLabelCoverageTotals(BaseModel):
    axes: int
    rows: int


class AxisLabelCoverageResponse(BaseModel):
    axes: list[AxisLabelCoverage]
    totals: AxisLabelCoverageTotals


router = APIRouter(prefix="/api")


@router.get(
    "/axis/label-coverage",
    response_model=AxisLabelCoverageResponse,
)
def axis_label_coverage(
    session: Session = Depends(get_session),
) -> AxisLabelCoverageResponse:
    return get_axis_label_coverage(session)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    routes = {
        route.path: route
        for route in test_app.routes
        if getattr(route, "path", None) == "/api/axis/label-coverage"
    }
    assert "/api/axis/label-coverage" in routes
    assert "GET" in routes["/api/axis/label-coverage"].methods
    print("PASS")