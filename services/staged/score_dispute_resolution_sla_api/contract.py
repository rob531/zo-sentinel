from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import McpScoreDispute
from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api")


class DisputeResolutionSLAResponse(BaseModel):
    days: int
    total: int
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_reason: Dict[str, int] = Field(default_factory=dict)
    median_resolution_hours: Optional[float] = None
    p90_resolution_hours: Optional[float] = None
    series: List[Dict[str, Any]] = Field(default_factory=list)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clamp_days(days: int) -> int:
    return max(1, min(365, days))


def _percentile_90(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = 0.90 * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


@router.get(
    "/disputes/resolution-sla",
    response_model=DisputeResolutionSLAResponse,
)
def get_dispute_resolution_sla(
    days: int = Query(default=30),
    session=Depends(get_session),
) -> DisputeResolutionSLAResponse:
    safe_days = _clamp_days(days)
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=safe_days)).replace(tzinfo=None)

    disputes = session.query(McpScoreDispute).filter(
        McpScoreDispute.created_at >= cutoff
    ).all()

    by_status: Dict[str, int] = {}
    by_reason: Dict[str, int] = {}
    resolution_hours: List[float] = []
    daily: Dict[str, Dict[str, int]] = {}

    for dispute in disputes:
        status = str(dispute.status or "unknown")
        by_status[status] = by_status.get(status, 0) + 1

        reason = str(dispute.reason_category or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1

        created_at = _as_utc(dispute.created_at)
        bucket = daily.setdefault(created_at.date().isoformat(), {"resolved": 0, "unresolved": 0})
        normalized_status = status.strip().lower()
        is_resolved = normalized_status == "resolved"

        if is_resolved:
            bucket["resolved"] += 1
            if dispute.resolved_at is not None:
                resolved_at = _as_utc(dispute.resolved_at)
                elapsed = (resolved_at - created_at).total_seconds() / 3600.0
                resolution_hours.append(max(0.0, elapsed))
        else:
            bucket["unresolved"] += 1

    series = [
        {"date": date, "resolved": values["resolved"], "unresolved": values["unresolved"]}
        for date, values in sorted(daily.items())
    ]

    return DisputeResolutionSLAResponse(
        days=safe_days,
        total=len(disputes),
        by_status=by_status,
        by_reason=by_reason,
        median_resolution_hours=(
            float(median(resolution_hours)) if resolution_hours else None
        ),
        p90_resolution_hours=_percentile_90(resolution_hours),
        series=series,
    )


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpScoreDispute.__table__.create(engine)
    now = datetime.now(timezone.utc)

    rows = [
        McpScoreDispute(
            id=1,
            server_id=1,
            submitted_by="analyst@example.com",
            proposed_overall_risk=0.8,
            proposed_axes={"trust": 0.7},
            reason_category="risk_scoring",
            explanation="Resolution completed after forty hours.",
            status="resolved",
            created_at=now - timedelta(hours=42),
            resolved_at=now - timedelta(hours=2),
        ),
        McpScoreDispute(
            id=2,
            server_id=1,
            submitted_by="analyst@example.com",
            proposed_overall_risk=0.6,
            proposed_axes={"trust": 0.5},
            reason_category="risk_scoring",
            explanation="Resolution completed after ten hours.",
            status="resolved",
            created_at=now - timedelta(hours=12),
            resolved_at=now - timedelta(hours=2),
        ),
        McpScoreDispute(
            id=3,
            server_id=2,
            submitted_by="reviewer@example.com",
            proposed_overall_risk=0.4,
            proposed_axes={"trust": 0.3},
            reason_category="axis_mismatch",
            explanation="Awaiting analyst review.",
            status="pending",
            created_at=now - timedelta(hours=3),
            resolved_at=None,
        ),
        McpScoreDispute(
            id=4,
            server_id=2,
            submitted_by="reviewer@example.com",
            proposed_overall_risk=0.5,
            proposed_axes={"trust": 0.4},
            reason_category="policy_context",
            explanation="Escalated for additional policy context.",
            status="escalated",
            created_at=now - timedelta(hours=4),
            resolved_at=None,
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/disputes/resolution-sla", params={"days": 30})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert payload["total"] == 4
    assert payload["by_status"]["resolved"] == 2
    assert payload["by_status"]["pending"] == 1
    assert payload["median_resolution_hours"] == 25.0
    assert payload["p90_resolution_hours"] == 37.0
    assert len(payload["series"]) >= 1
    assert sum(item["resolved"] for item in payload["series"]) == 2
    assert sum(item["unresolved"] for item in payload["series"]) == 2


if __name__ == "__main__":
    _self_test()
    print("PASS")