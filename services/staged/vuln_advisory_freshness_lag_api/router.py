from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import VulnAdvisory

try:
    from .logic import get_freshness_lag as _get_freshness_lag
except ImportError:
    try:
        from .logic import get_freshness_lag_report as _get_freshness_lag
    except ImportError:
        try:
            from .logic import get_freshness_lag_data as _get_freshness_lag
        except ImportError:
            try:
                from .logic import build_freshness_lag_report as _get_freshness_lag
            except ImportError:
                from .logic import fetch_freshness_lag as _get_freshness_lag


class FreshnessLagBucket(BaseModel):
    bucket: str
    count: int = Field(ge=0)


class FreshnessLagFeed(BaseModel):
    feed: str
    total: int = Field(ge=0)
    stale_90plus: int = Field(ge=0)
    worst_lag_hours: Optional[float] = None


class FreshnessLagOverall(BaseModel):
    total: int = Field(ge=0)
    stale_count: int = Field(ge=0)
    median_lag_hours: Optional[float] = None
    p95_lag_hours: Optional[float] = None


class FreshnessLagResponse(BaseModel):
    buckets: List[FreshnessLagBucket]
    feeds: List[FreshnessLagFeed]
    overall: FreshnessLagOverall


router = APIRouter(prefix="/api")


@router.get(
    "/vuln/advisories/freshness-lag",
    response_model=FreshnessLagResponse,
    summary="Vulnerability advisory freshness lag",
)
def get_freshness_lag(session=Depends(get_session)) -> FreshnessLagResponse:
    result = _get_freshness_lag(session)

    if isinstance(result, BaseModel):
        payload: Dict[str, Any] = (
            result.model_dump() if hasattr(result, "model_dump") else result.dict()
        )
    elif isinstance(result, Mapping):
        payload = dict(result)
    else:
        payload = {
            "buckets": result.buckets,
            "feeds": result.feeds,
            "overall": result.overall,
        }

    return FreshnessLagResponse(
        buckets=payload["buckets"],
        feeds=payload["feeds"],
        overall=payload["overall"],
    )


if __name__ == "__main__":
    assert any(
        getattr(route, "path", None) == "/api/vuln/advisories/freshness-lag"
        for route in router.routes
    )
    print("PASS")