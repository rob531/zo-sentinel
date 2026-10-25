from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import *


router = APIRouter(prefix="/api")

AGE_BANDS = ("0-24h", "24-72h", "72h-7d", "over-7d")


class DisputeQueueAgeingResponse(BaseModel):
    total_open: int
    oldest_open_hours: float
    by_age_band: Dict[str, int]
    by_reason_category: Dict[str, int]


def _find_logic_function():
    candidates = (
        "get_dispute_queue_ageing",
        "get_queue_ageing",
        "compute_dispute_queue_ageing",
        "calculate_dispute_queue_ageing",
        "build_dispute_queue_ageing",
        "get_dispute_ageing",
        "get_queue_ageing_summary",
    )
    for name in candidates:
        function = globals().get(name)
        if callable(function):
            return function

    for name, function in globals().items():
        if not callable(function):
            continue
        if getattr(function, "__module__", "").endswith(".logic"):
            lowered = name.lower()
            if (
                ("queue" in lowered and ("age" in lowered or "ageing" in lowered))
                or ("dispute" in lowered and ("age" in lowered or "ageing" in lowered))
            ):
                return function

    raise RuntimeError("No dispute queue-ageing function found in .logic")


_queue_ageing_logic = _find_logic_function()


@router.get(
    "/disputes/queue-ageing",
    response_model=DisputeQueueAgeingResponse,
)
def read_dispute_queue_ageing(session: Session = Depends(get_session)) -> DisputeQueueAgeingResponse:
    result = _queue_ageing_logic(session)
    payload = dict(result)

    raw_bands = payload.get("by_age_band") or {}
    raw_categories = payload.get("by_reason_category") or {}

    return DisputeQueueAgeingResponse(
        total_open=int(payload.get("total_open", 0)),
        oldest_open_hours=float(payload.get("oldest_open_hours", 0)),
        by_age_band={band: int(raw_bands.get(band, 0)) for band in AGE_BANDS},
        by_reason_category={
            str(category): int(count) for category, count in raw_categories.items()
        },
    )


if __name__ == "__main__":
    assert any(
        getattr(route, "path", None) == "/api/disputes/queue-ageing"
        for route in router.routes
    )
    print("PASS")