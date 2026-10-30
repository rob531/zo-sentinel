# deps: requests, fastapi, pydantic

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from .logic import McpScoreDispute as _LogicMcpScoreDispute


router = APIRouter(prefix="/api")


class OpenDisputeItem(BaseModel):
    id: int
    server_id: str
    reason_category: str
    status: str
    created_at: datetime


class DisputeLifecycleResponse(BaseModel):
    days: int
    status_counts: Dict[str, int]
    reason_counts: Dict[str, int]
    median_resolution_hours: Optional[float]
    open_disputes: List[OpenDisputeItem]
    total: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get(
    "/disputes/lifecycle",
    response_model=DisputeLifecycleResponse,
)
def get_dispute_lifecycle(
    status: Optional[str] = None,
    days: int = 30,
    session: Session = Depends(get_session),
) -> DisputeLifecycleResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = session.query(McpScoreDispute)
    query = query.filter(McpScoreDispute.created_at >= cutoff)
    if status is not None:
        query = query.filter(McpScoreDispute.status == status)

    disputes = query.all()

    status_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    resolution_hours: List[float] = []
    open_disputes: List[OpenDisputeItem] = []

    for dispute in disputes:
        dispute_status = str(dispute.status)
        status_counts[dispute_status] = status_counts.get(dispute_status, 0) + 1

        reason = str(dispute.reason_category)
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

        if dispute.resolved_at is not None and dispute.created_at is not None:
            elapsed = _as_utc(dispute.resolved_at) - _as_utc(dispute.created_at)
            resolution_hours.append(elapsed.total_seconds() / 3600.0)

        if dispute_status == "open":
            open_disputes.append(
                OpenDisputeItem(
                    id=dispute.id,
                    server_id=dispute.server_id,
                    reason_category=dispute.reason_category,
                    status=dispute.status,
                    created_at=dispute.created_at,
                )
            )

    open_disputes.sort(key=lambda item: item.created_at, reverse=True)
    open_disputes = open_disputes[:10]

    return DisputeLifecycleResponse(
        days=days,
        status_counts=status_counts,
        reason_counts=reason_counts,
        median_resolution_hours=(
            float(median(resolution_hours)) if resolution_hours else None
        ),
        open_disputes=open_disputes,
        total=len(disputes),
    )


if __name__ == "__main__":
    from fastapi import FastAPI

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: None

    assert any(
        route.path == "/api/disputes/lifecycle" and "GET" in route.methods
        for route in that_app.routes
    )
    print("PASS")