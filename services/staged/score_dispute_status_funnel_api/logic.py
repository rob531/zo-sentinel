from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


class StatusCount(BaseModel):
    status: str
    count: int


class DisputeTotals(BaseModel):
    disputes: int
    resolved: int
    open: int
    tier_change_requests: int
    missing_axes: int


class ResolutionHours(BaseModel):
    median: Optional[float]
    count: int


class OpenAgeBucket(BaseModel):
    bucket: str
    count: int


class DisputeStatusFunnelResponse(BaseModel):
    generated_at: str
    by_status: List[StatusCount]
    totals: DisputeTotals
    resolution_hours: ResolutionHours
    open_age_buckets: List[OpenAgeBucket]


OPEN_AGE_BUCKETS = (
    "0-24h",
    "24-72h",
    "72-168h",
    "168h+",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalized_tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value).strip().casefold()


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None

    ordered = sorted(values)
    middle = len(ordered) // 2

    if len(ordered) % 2:
        return float(ordered[middle])

    return float((ordered[middle - 1] + ordered[middle]) / 2.0)


def compute_dispute_status_funnel(
    session: Session,
    status: Optional[str] = None,
    days: int = 90,
) -> Dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    generated_at = _utcnow()
    cutoff = generated_at - timedelta(days=days)

    statement = (
        select(
            McpScoreDispute.status,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.proposed_axes,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpServerRegistry.risk_tier,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
        .where(McpScoreDispute.created_at >= cutoff)
        .order_by(McpScoreDispute.status, McpScoreDispute.id)
    )

    if status is not None:
        statement = statement.where(McpScoreDispute.status == status)

    rows = session.execute(statement).all()

    status_counts: Dict[str, int] = {}
    tier_change_requests = 0
    missing_axes = 0
    resolved = 0
    open_count = 0
    resolution_hours: List[float] = []
    open_age_counts = {bucket: 0 for bucket in OPEN_AGE_BUCKETS}

    for (
        dispute_status,
        proposed_overall_risk,
        proposed_axes,
        created_at,
        resolved_at,
        current_risk_tier,
    ) in rows:
        status_label = "unknown" if dispute_status is None else str(dispute_status)
        status_counts[status_label] = status_counts.get(status_label, 0) + 1

        proposed_tier = _normalized_tier(proposed_overall_risk)
        current_tier = _normalized_tier(current_risk_tier)
        if (
            proposed_tier is not None
            and current_tier is not None
            and proposed_tier != current_tier
        ):
            tier_change_requests += 1

        if proposed_axes is None:
            missing_axes += 1

        if resolved_at is not None:
            resolved += 1
            if created_at is not None:
                elapsed = _as_utc(resolved_at) - _as_utc(created_at)
                resolution_hours.append(max(0.0, elapsed.total_seconds() / 3600.0))
        else:
            open_count += 1
            if created_at is not None:
                age = max(
                    0.0,
                    (generated_at - _as_utc(created_at)).total_seconds() / 3600.0,
                )
                if age <= 24:
                    open_age_counts["0-24h"] += 1
                if 24 <= age <= 72:
                    open_age_counts["24-72h"] += 1
                if 72 <= age <= 168:
                    open_age_counts["72-168h"] += 1
                if age > 168:
                    open_age_counts["168h+"] += 1

    return {
        "generated_at": generated_at.isoformat(),
        "by_status": [
            {"status": status_value, "count": count}
            for status_value, count in sorted(status_counts.items())
        ],
        "totals": {
            "disputes": len(rows),
            "resolved": resolved,
            "open": open_count,
            "tier_change_requests": tier_change_requests,
            "missing_axes": missing_axes,
        },
        "resolution_hours": {
            "median": _median(resolution_hours),
            "count": len(resolution_hours),
        },
        "open_age_buckets": [
            {"bucket": bucket, "count": open_age_counts[bucket]}
            for bucket in OPEN_AGE_BUCKETS
        ],
    }


get_dispute_status_funnel = compute_dispute_status_funnel
get_status_funnel = compute_dispute_status_funnel


router = APIRouter(prefix="/api")


@router.get(
    "/disputes/funnel",
    response_model=DisputeStatusFunnelResponse,
    operation_id="get_dispute_status_funnel",
)
def get_dispute_funnel(
    status: Optional[str] = Query(default=None),
    days: int = Query(default=90, ge=1, le=36500),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return compute_dispute_status_funnel(session, status=status, days=days)


app = FastAPI(title="Score Dispute Status Funnel API")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import logic as self_module

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
    )
    McpServerRegistry.__table__.create(test_engine)
    McpScoreDispute.__table__.create(test_engine)
    SessionFactory = sessionmaker(bind=test_engine)

    fixed_now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    original_utcnow = self_module._utcnow
    self_module._utcnow = lambda: fixed_now

    test_session = SessionFactory()
    try:
        test_session.add_all(
            [
                McpServerRegistry(
                    server_id="server-a",
                    name="Server A",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-b",
                    name="Server B",
                    risk_tier="high",
                ),
                McpScoreDispute(
                    server_id="server-a",
                    submitted_by="user-a",
                    proposed_overall_risk="low",
                    proposed_axes={"truthfulness": 0.8},
                    reason_category="misclassification",
                    status="resolved",
                    admin_note="Adjusted",
                    created_at=fixed_now - timedelta(hours=30),
                    resolved_at=fixed_now - timedelta(hours=24),
                ),
                McpScoreDispute(
                    server_id="server-b",
                    submitted_by="user-b",
                    proposed_overall_risk="critical",
                    proposed_axes=None,
                    reason_category="new_evidence",
                    status="resolved",
                    admin_note="Confirmed",
                    created_at=fixed_now - timedelta(hours=120),
                    resolved_at=fixed_now - timedelta(hours=100),
                ),
                McpScoreDispute(
                    server_id="server-a",
                    submitted_by="user-c",
                    proposed_overall_risk="high",
                    proposed_axes={"safety": 0.4},
                    reason_category="context_change",
                    status="open",
                    admin_note=None,
                    created_at=fixed_now - timedelta(hours=24),
                    resolved_at=None,
                ),
            ]
        )
        test_session.commit()
    finally:
        test_session.close()

    def override_get_session():
        session = SessionFactory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/disputes/funnel")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["totals"]["disputes"] == 3
            assert payload["totals"]["resolved"] == 2
            assert payload["totals"]["open"] == 1
            assert payload["resolution_hours"]["count"] == 2
            assert len(
                [bucket for bucket in payload["open_age_buckets"] if bucket["count"] > 0]
            ) == 2
    finally:
        app.dependency_overrides.clear()
        self_module._utcnow = original_utcnow
        test_engine.dispose()

    print("PASS")