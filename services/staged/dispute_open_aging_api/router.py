from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import get_open_aging


router = APIRouter(prefix="/api", tags=["disputes"])


class AgingBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)


class DisputeOpenAgingResponse(BaseModel):
    generated_at: str
    days_window: int = Field(ge=0)
    total_open: int = Field(ge=0)
    buckets: list[AgingBucket]
    oldest_open_days: float | None = Field(default=None, ge=0)
    by_reason_category: dict[str, int]


@router.get(
    "/disputes/open-aging",
    response_model=DisputeOpenAgingResponse,
    summary="Open dispute aging report",
)
def get_dispute_open_aging(
    days: int = Query(30, ge=0),
    session: Session = Depends(get_session),
) -> DisputeOpenAgingResponse:
    result = get_open_aging(session=session, days=days)
    return DisputeOpenAgingResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = lambda: None

    def _self_test_data(session: Session, days: int) -> dict:
        return {
            "generated_at": "2024-01-01T00:00:00Z",
            "days_window": days,
            "total_open": 1,
            "buckets": [{"bucket": "[0,1)", "count": 1}],
            "oldest_open_days": 0.5,
            "by_reason_category": {"risk": 1},
        }

    original_get_open_aging = get_open_aging
    get_open_aging = _self_test_data
    try:
        with TestClient(application) as client:
            response = client.get("/api/disputes/open-aging?days=30")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_open"] == 1
            assert payload["buckets"] == [{"bucket": "[0,1)", "count": 1}]
            assert payload["by_reason_category"] == {"risk": 1}
    finally:
        get_open_aging = original_get_open_aging

    print("PASS")