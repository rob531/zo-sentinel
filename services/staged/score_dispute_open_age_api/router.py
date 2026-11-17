from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_open_age_breakdown
except ImportError:
    from .logic import get_open_dispute_age_breakdown as get_open_age_breakdown


router = APIRouter(prefix="/api", tags=["disputes"])


class AgeBucketResponse(BaseModel):
    label: str
    count: int


class OpenAgeResponse(BaseModel):
    open_count: int
    buckets: List[AgeBucketResponse]
    oldest_created_at: Optional[str]
    mean_age_days: float


def _iso_timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@router.get("/disputes/open-age", response_model=OpenAgeResponse)
def open_age(session: Session = Depends(get_session)) -> dict:
    result = get_open_age_breakdown(session)
    return {
        "open_count": int(result["open_count"]),
        "buckets": [
            {"label": str(bucket["label"]), "count": int(bucket["count"])}
            for bucket in result["buckets"]
        ],
        "oldest_created_at": _iso_timestamp(result.get("oldest_created_at")),
        "mean_age_days": float(result["mean_age_days"]),
    }


if __name__ == "__main__":
    assert any(
        getattr(route, "path", None) == "/api/disputes/open-age"
        for route in router.routes
    )
    print("PASS")