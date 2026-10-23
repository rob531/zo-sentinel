from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_risk_register_trend

router = APIRouter(prefix="/api")


class RiskRegisterTrendPoint(BaseModel):
    date: str
    row_count: int
    distinct_servers: int
    mean_score: float


class RiskRegisterTrendTotals(BaseModel):
    rows: int
    servers: int


class RiskRegisterTrendResponse(BaseModel):
    days: int
    series: List[RiskRegisterTrendPoint]
    totals: RiskRegisterTrendTotals


@router.get(
    "/risk-register/trend",
    response_model=RiskRegisterTrendResponse,
    summary="Get the risk-register trend",
)
def risk_register_trend(
    days: int = Query(default=30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> RiskRegisterTrendResponse:
    result = get_risk_register_trend(session, days)
    return RiskRegisterTrendResponse(
        days=days,
        series=result["series"],
        totals=result["totals"],
    )


if __name__ == "__main__":
    sample = RiskRegisterTrendResponse(
        days=2,
        series=[
            RiskRegisterTrendPoint(
                date="2025-01-01",
                row_count=2,
                distinct_servers=1,
                mean_score=75.0,
            ),
            RiskRegisterTrendPoint(
                date="2025-01-02",
                row_count=2,
                distinct_servers=1,
                mean_score=80.0,
            ),
        ],
        totals=RiskRegisterTrendTotals(rows=4, servers=2),
    )
    assert sample.days == 2
    assert len(sample.series) == 2
    assert sample.series[0].row_count == 2
    assert sample.totals.rows == 4
    assert sample.totals.servers == 2
    print("PASS")