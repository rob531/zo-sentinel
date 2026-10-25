from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_stats_history

router = APIRouter(prefix="/api", tags=["freshness"])


class FreshnessSnapshot(BaseModel):
    computed_at: datetime
    compute_seconds: float
    payload: dict[str, Any]


class FreshnessHistoryEntry(BaseModel):
    computed_at: datetime
    compute_seconds: float


class FreshnessStatsHistoryResponse(BaseModel):
    surface: str
    count: int
    latest: FreshnessSnapshot
    history: list[FreshnessHistoryEntry]


@router.get(
    "/freshness/stats/history",
    response_model=FreshnessStatsHistoryResponse,
)
def freshness_stats_history(
    surface: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
) -> FreshnessStatsHistoryResponse:
    result = get_freshness_stats_history(
        session=session,
        surface=surface,
        limit=limit,
    )
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No freshness stats found for surface '{surface}'",
        )
    return FreshnessStatsHistoryResponse.model_validate(result)


if __name__ == "__main__":
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    print("PASS")