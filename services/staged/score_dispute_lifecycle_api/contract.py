# deps: requests, fastapi, pydantic

from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


class OpenDisputeItem(BaseModel):
    id: int
    server_id: int
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


router = APIRouter(prefix="/api")


@router.get(
    "/disputes/lifecycle",
    response_model=DisputeLifecycleResponse,
)
def get_dispute_lifecycle(
    days: int = Query(default=30, ge=0),
    status: Optional[str] = Query(default=None),
    session=Depends(get_session),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = session.query(McpScoreDispute)
    query = query.filter(McpScoreDispute.created_at >= cutoff)
    if status is not None:
        query = query.filter(McpScoreDispute.status == status)

    disputes = query.all()
    status_counts = Counter(dispute.status for dispute in disputes)
    reason_counts = Counter(dispute.reason_category for dispute in disputes)

    resolution_hours = []
    for dispute in disputes:
        if dispute.resolved_at is None:
            continue
        created_at = dispute.created_at
        resolved_at = dispute.resolved_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if resolved_at.tzinfo is None:
            resolved_at = resolved_at.replace(tzinfo=timezone.utc)
        resolution_hours.append(
            (resolved_at - created_at).total_seconds() / 3600.0
        )

    open_disputes = [
        OpenDisputeItem(
            id=dispute.id,
            server_id=dispute.server_id,
            reason_category=dispute.reason_category,
            status=dispute.status,
            created_at=dispute.created_at,
        )
        for dispute in sorted(
            (dispute for dispute in disputes if dispute.status == "open"),
            key=lambda dispute: dispute.created_at,
            reverse=True,
        )[:10]
    ]

    return DisputeLifecycleResponse(
        days=days,
        status_counts=dict(status_counts),
        reason_counts=dict(reason_counts),
        median_resolution_hours=(
            float(median(resolution_hours)) if resolution_hours else None
        ),
        open_disputes=open_disputes,
        total=len(disputes),
    )


def _run_self_test():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(bind=engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    resolved_at = now + timedelta(hours=2)

    with testing_session() as session:
        session.execute(
            McpScoreDispute.__table__.insert().values(
                id=1,
                server_id=101,
                submitted_by=1,
                proposed_overall_risk=0.8,
                proposed_axes={"quality": 0.8},
                explanation="acceptance fixture",
                reason_category="accuracy",
                status="resolved",
                admin_note="Reviewed",
                created_at=now,
                resolved_at=resolved_at,
            )
        )
        session.execute(
            McpScoreDispute.__table__.insert().values(
                id=2,
                server_id=102,
                submitted_by=1,
                proposed_overall_risk=0.6,
                proposed_axes={"quality": 0.6},
                explanation="acceptance fixture",
                reason_category="coverage",
                status="open",
                admin_note=None,
                created_at=now,
                resolved_at=None,
            )
        )
        session.execute(
            McpScoreDispute.__table__.insert().values(
                id=3,
                server_id=103,
                submitted_by=1,
                proposed_overall_risk=0.4,
                proposed_axes={"quality": 0.4},
                explanation="acceptance fixture",
                reason_category="accuracy",
                status="closed",
                admin_note="Closed",
                created_at=now,
                resolved_at=None,
            )
        )
        session.commit()

    client = TestClient(that_app)
    response = client.get("/api/disputes/lifecycle")
    assert response.status_code == 200
    payload = response.json()

    assert payload["total"] == 3
    assert "resolved" in payload["status_counts"]
    assert "open" in payload["status_counts"]
    assert "closed" in payload["status_counts"]
    assert abs(payload["median_resolution_hours"] - 2.0) <= 0.01
    assert len(payload["open_disputes"]) == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()