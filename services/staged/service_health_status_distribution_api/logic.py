from datetime import datetime, timezone
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session


class StatusCount(BaseModel):
    status: Optional[str] = None
    count: int
    oldest_age_seconds: Optional[float] = None


class StatusDistributionResponse(BaseModel):
    generated_at: datetime
    total: int
    by_status: List[StatusCount]


router = APIRouter(prefix="/api", tags=["health"])
app = FastAPI()
app.include_router(router)


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            try:
                dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_status_distribution(session: Any) -> StatusDistributionResponse:
    rows = session.execute(
        text(
            "SELECT status, COUNT(*) AS cnt, MIN(last_heartbeat) AS oldest_heartbeat "
            "FROM service_health GROUP BY status"
        )
    ).mappings().all()

    by_status: List[StatusCount] = []
    total = 0
    for row in rows:
        count = int(row["cnt"] or 0)
        total += count
        oldest = _as_datetime(row["oldest_heartbeat"])
        age = None
        if oldest is not None:
            age = (datetime.now(timezone.utc) - oldest).total_seconds()
        by_status.append(
            StatusCount(
                status=row["status"],
                count=count,
                oldest_age_seconds=age,
            )
        )

    by_status.sort(key=lambda item: (item.status is None, str(item.status)))
    return StatusDistributionResponse(
        generated_at=datetime.now(timezone.utc),
        total=total,
        by_status=by_status,
    )


@router.get("/health/status-distribution", response_model=StatusDistributionResponse)
def get_status_distribution(session: Any = Depends(get_session)) -> StatusDistributionResponse:
    return compute_status_distribution(session)


status_distribution = get_status_distribution
get_health_status_distribution = get_status_distribution
health_status_distribution = get_status_distribution
run = get_status_distribution


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)