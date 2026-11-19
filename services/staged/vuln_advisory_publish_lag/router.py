from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import advisory_publish_lag as _advisory_publish_lag
except ImportError:
    try:
        from .logic import get_advisory_lag as _advisory_publish_lag
    except ImportError:
        from .logic import advisory_lag as _advisory_publish_lag

router = APIRouter(prefix="/api/vuln", tags=["vuln"])


class LagBuckets(BaseModel):
    under_1h: int
    one_to_six_h: int
    six_to_24h: int
    over_24h: int
    unknown: int


class OverallLag(BaseModel):
    total: int
    buckets: LagBuckets
    median_lag_hours: float | None
    p95_lag_hours: float | None


class FeedLag(BaseModel):
    feed: str
    total: int
    over_24h: int
    median_lag_hours: float | None


class OldestAdvisory(BaseModel):
    advisory_id: str
    feed: str
    severity: str
    lag_hours: float | None


class AdvisoryLagResponse(BaseModel):
    days: int
    overall: OverallLag
    by_feed: list[FeedLag]
    oldest: list[OldestAdvisory]
    linked_server_count: int


@router.get("/advisory-lag", response_model=AdvisoryLagResponse)
def get_advisory_lag(
    db: Session = Depends(get_session),
    days: int = Query(default=30, ge=1, le=365),
) -> AdvisoryLagResponse:
    result = _advisory_publish_lag(db, days)
    return AdvisoryLagResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    _sample_result = {
        "days": 30,
        "overall": {
            "total": 0,
            "buckets": {
                "under_1h": 0,
                "one_to_six_h": 0,
                "six_to_24h": 0,
                "over_24h": 0,
                "unknown": 0,
            },
            "median_lag_hours": None,
            "p95_lag_hours": None,
        },
        "by_feed": [],
        "oldest": [],
        "linked_server_count": 0,
    }

    def _test_compute(db, days):
        return _sample_result

    globals()["_advisory_publish_lag"] = _test_compute
    response = TestClient(test_app).get("/api/vuln/advisory-lag")
    response.raise_for_status()
    assert response.json()["overall"]["total"] == 0
    print("PASS")