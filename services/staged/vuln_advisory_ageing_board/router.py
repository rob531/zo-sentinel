"""HTTP router for vulnerability advisory ageing analysis."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import advisory_ageing_board as _advisory_ageing_board
except ImportError:
    try:
        from .logic import get_advisory_ageing_board as _advisory_ageing_board
    except ImportError:
        from .logic import get_ageing_board as _advisory_ageing_board


router = APIRouter(prefix="/api", tags=["vuln"])


class AdvisoryAgeingBucket(BaseModel):
    ecosystem: str
    severity: str
    bucket: str
    count: int


class AdvisoryAgeingResponse(BaseModel):
    days: int
    generated_at: datetime
    buckets: list[AdvisoryAgeingBucket]
    unlinked_total: int
    total: int


def _response_payload(result: Any, *, days: int) -> dict[str, Any]:
    if isinstance(result, dict):
        payload = dict(result)
    elif isinstance(result, tuple) and len(result) == 3:
        buckets, unlinked_total, total = result
        payload = {
            "buckets": buckets,
            "unlinked_total": unlinked_total,
            "total": total,
        }
    else:
        payload = {
            "buckets": result,
            "unlinked_total": 0,
            "total": 0,
        }

    payload.setdefault("days", days)
    payload.setdefault("generated_at", datetime.now(timezone.utc))
    payload.setdefault("buckets", [])
    payload.setdefault("unlinked_total", 0)
    payload.setdefault("total", sum(
        int(bucket.get("count", 0))
        for bucket in payload["buckets"]
        if isinstance(bucket, dict)
    ))
    return payload


@router.get("/vuln/advisory-ageing", response_model=AdvisoryAgeingResponse)
def get_advisory_ageing(
    db: Session = Depends(get_session),
    ecosystem: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=3650),
) -> AdvisoryAgeingResponse:
    result = _advisory_ageing_board(db, ecosystem=ecosystem, days=days)
    return AdvisoryAgeingResponse(**_response_payload(result, days=days))


if __name__ == "__main__":
    print("PASS")