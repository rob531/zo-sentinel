"""logic.py -- the service's data/computation layer (one concern, one file)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


_STATUSES = ("open", "in_review", "approved", "rejected", "escalated")


def _utc_datetime(value: datetime) -> datetime:
    """Return a datetime normalized to UTC, including naive database values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _date_value(value: datetime | None) -> date | None:
    if value is None:
        return None
    return _utc_datetime(value).date()


def get_status_breakdown(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return dispute status, tier-mismatch, age, and daily activity statistics.

    The registry is deliberately joined with an outer join so disputes with an
    unknown server remain part of the status and activity totals.
    """
    if not 1 <= int(days) <= 365:
        raise ValueError("days must be between 1 and 365")

    days = int(days)
    now = datetime.now(timezone.utc)
    today = now.date()
    start_date = today - timedelta(days=days - 1)

    rows = db.execute(
        select(
            McpScoreDispute.status,
            McpScoreDispute.proposed_overall_risk,
            McpScoreDispute.created_at,
            McpScoreDispute.resolved_at,
            McpServerRegistry.risk_tier,
        )
        .select_from(McpScoreDispute)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpScoreDispute.server_id,
        )
    ).all()

    status_counts: Dict[str, int] = {status: 0 for status in _STATUSES}
    tier_mismatch_count = 0
    oldest_open_created_at: datetime | None = None

    submitted_by_date: Dict[date, int] = {}
    resolved_by_date: Dict[date, int] = {}

    for status, proposed_risk, created_at, resolved_at, risk_tier in rows:
        if status is not None:
            normalized_status = str(status).strip().lower()
            if normalized_status in status_counts:
                status_counts[normalized_status] += 1

        if (
            proposed_risk is not None
            and risk_tier is not None
            and str(proposed_risk) != str(risk_tier)
        ):
            tier_mismatch_count += 1

        created_date = _date_value(created_at)
        if created_date is not None:
            if start_date <= created_date <= today:
                submitted_by_date[created_date] = (
                    submitted_by_date.get(created_date, 0) + 1
                )
            if status is not None and str(status).strip().lower() == "open":
                if oldest_open_created_at is None or created_at < oldest_open_created_at:
                    oldest_open_created_at = created_at

        resolved_date = _date_value(resolved_at)
        if resolved_date is not None and start_date <= resolved_date <= today:
            resolved_by_date[resolved_date] = resolved_by_date.get(resolved_date, 0) + 1

    if oldest_open_created_at is None:
        oldest_open_age_days = 0.0
    else:
        age = now - _utc_datetime(oldest_open_created_at)
        oldest_open_age_days = round(max(0.0, age.total_seconds() / 86400.0), 4)

    submitted_series: List[Dict[str, Any]] = []
    for current_date in (
        start_date + timedelta(days=offset) for offset in range(days)
    ):
        submitted_series.append(
            {
                "date": current_date.isoformat(),
                "submitted": submitted_by_date.get(current_date, 0),
                "resolved": resolved_by_date.get(current_date, 0),
            }
        )

    return {
        "days": days,
        "status_counts": status_counts,
        "tier_mismatch_count": tier_mismatch_count,
        "oldest_open_age_days": oldest_open_age_days,
        "submitted_series": submitted_series,
        "generated_at": now.isoformat(),
    }


def get_dispute_status_breakdown(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the service-specific name."""
    return get_status_breakdown(db, days)


def build_status_breakdown(db: Session, days: int = 30) -> Dict[str, Any]:
    """Compatibility alias for callers using the builder-style name."""
    return get_status_breakdown(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-a", risk_tier="low"),
                McpServerRegistry(server_id="server-b", risk_tier="medium"),
            ]
        )
        session.commit()

        base = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id="server-a",
                    proposed_overall_risk="low",
                    reason_category="appeal",
                    explanation="seeded open dispute",
                    status="open",
                    created_at=base,
                    submitted_by="seed-user",
                ),
                McpScoreDispute(
                    id=2,
                    server_id="server-a",
                    proposed_overall_risk="low",
                    reason_category="appeal",
                    explanation="seeded resolved dispute",
                    status="approved",
                    created_at=base - timedelta(days=1),
                    resolved_at=base - timedelta(days=1),
                    submitted_by="seed-user",
                ),
                McpScoreDispute(
                    id=3,
                    server_id="server-b",
                    proposed_overall_risk="high",
                    reason_category="appeal",
                    explanation="seeded tier mismatch",
                    status="rejected",
                    created_at=base - timedelta(days=2),
                    resolved_at=base - timedelta(days=2),
                    submitted_by="seed-user",
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/disputes/status-breakdown")
    def status_breakdown_route(db: Session = Depends(get_session), days: int = 30):
        return get_status_breakdown(db, days)

    response = TestClient(that_app).get(
        "/api/disputes/status-breakdown?days=30"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status_counts"]["open"] == 1
    assert payload["tier_mismatch_count"] == 1
    assert len(payload["submitted_series"]) == 30
    assert any(item["submitted"] > 0 for item in payload["submitted_series"])
    print("PASS")