"""Data and computation layer for the score dispute lifecycle summary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


_TERMINAL_STATUSES = {
    "approved",
    "closed",
    "dismissed",
    "rejected",
    "resolved",
}


def _as_utc(value: datetime) -> datetime:
    """Normalize database datetimes to UTC for consistent calculations."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> int:
    delta = now - _as_utc(created_at)
    return max(0, int(delta.total_seconds() // 86400))


def _is_open(status: Optional[str]) -> bool:
    return (status or "").strip().lower() not in _TERMINAL_STATUSES


def get_dispute_summary(
    db: Session,
    status: Optional[str] = None,
    days: int = 90,
) -> Dict:
    """Summarize disputes from the application database.

    The time window is applied to the dispute creation time. Ageing and
    resolution durations are calculated from the persisted timestamps.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    statement = (
        select(McpScoreDispute, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.created_at >= cutoff)
        .order_by(McpScoreDispute.created_at.asc())
    )
    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    rows = db.execute(statement).all()

    by_status: Dict[str, int] = {}
    by_reason_category: Dict[str, int] = {}
    ageing_open = {"lt_7": 0, "7_to_30": 0, "gt_30": 0}
    resolution_hours: List[float] = []
    oldest_open: List[Dict] = []

    for dispute, registry in rows:
        dispute_status = dispute.status or "unknown"
        by_status[dispute_status] = by_status.get(dispute_status, 0) + 1

        reason_category = dispute.reason_category or "unknown"
        by_reason_category[reason_category] = (
            by_reason_category.get(reason_category, 0) + 1
        )

        created_at = _as_utc(dispute.created_at)
        age_days = _age_days(created_at, now)

        if _is_open(dispute.status):
            if age_days < 7:
                ageing_open["lt_7"] += 1
            elif age_days <= 30:
                ageing_open["7_to_30"] += 1
            else:
                ageing_open["gt_30"] += 1

            oldest_open.append(
                {
                    "id": int(dispute.id),
                    "server_id": str(dispute.server_id),
                    "server_name": registry.name if registry is not None else None,
                    "status": dispute_status,
                    "age_days": age_days,
                    "proposed_overall_risk": dispute.proposed_overall_risk,
                }
            )

        if dispute.resolved_at is not None:
            resolved_at = _as_utc(dispute.resolved_at)
            resolution_hours.append(
                max(0.0, (resolved_at - created_at).total_seconds() / 3600.0)
            )

    oldest_open.sort(key=lambda item: (-item["age_days"], item["id"]))

    if resolution_hours:
        median_resolution_hours = float(median(sorted(resolution_hours)))
    else:
        median_resolution_hours = None

    return {
        "days": int(days),
        "by_status": [
            {"status": key, "count": value}
            for key, value in sorted(by_status.items())
        ],
        "by_reason_category": [
            {"reason_category": key, "count": value}
            for key, value in sorted(by_reason_category.items())
        ],
        "ageing_open": ageing_open,
        "median_resolution_hours": median_resolution_hours,
        "oldest_open": oldest_open[:10],
    }


# Kept as a descriptive alias for callers that use the service's full name.
get_score_dispute_lifecycle_summary = get_dispute_summary


router = APIRouter()


@router.get("/disputes/summary")
def score_dispute_summary(
    status: Optional[str] = Query(default=None),
    days: int = Query(default=90, ge=1),
    db: Session = Depends(get_session),
) -> Dict:
    return get_dispute_summary(db=db, status=status, days=days)


app = FastAPI(title="score_dispute_lifecycle_summary")
app.include_router(router, prefix="/api")


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    test_now = datetime.now(timezone.utc).replace(microsecond=0)
    with TestSession() as test_db:
        test_db.add_all(
            [
                McpScoreDispute(
                    server_id="server-1",
                    submitted_by="analyst",
                    proposed_overall_risk="high",
                    proposed_axes=None,
                    reason_category="scoring",
                    status="open",
                    admin_note=None,
                    explanation="",
                    created_at=test_now - timedelta(days=60),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="server-2",
                    submitted_by="analyst",
                    proposed_overall_risk="medium",
                    proposed_axes=None,
                    reason_category="scoring",
                    status="open",
                    admin_note=None,
                    explanation="",
                    created_at=test_now - timedelta(days=10),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="server-3",
                    submitted_by="analyst",
                    proposed_overall_risk="low",
                    proposed_axes=None,
                    reason_category="scoring",
                    status="open",
                    admin_note=None,
                    explanation="",
                    created_at=test_now - timedelta(days=2),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id="server-4",
                    submitted_by="analyst",
                    proposed_overall_risk="high",
                    proposed_axes=None,
                    reason_category="scoring",
                    status="resolved",
                    admin_note="resolved",
                    explanation="",
                    created_at=test_now - timedelta(hours=4),
                    resolved_at=test_now,
                ),
                McpScoreDispute(
                    server_id="server-5",
                    submitted_by="analyst",
                    proposed_overall_risk="medium",
                    proposed_axes=None,
                    reason_category="scoring",
                    status="resolved",
                    admin_note="resolved",
                    explanation="",
                    created_at=test_now - timedelta(hours=4),
                    resolved_at=test_now,
                ),
            ]
        )
        test_db.commit()

    def override_get_session():
        with TestSession() as test_db:
            yield test_db

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/summary")
        assert response.status_code == 200
        payload = response.json()
        assert sum(item["count"] for item in payload["by_status"]) == 5
        assert sum(payload["ageing_open"].values()) == 3
        assert payload["median_resolution_hours"] == 4.0

    print("PASS")