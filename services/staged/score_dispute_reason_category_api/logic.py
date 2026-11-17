from collections import Counter
from datetime import datetime, timedelta, timezone
from math import floor
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_MAX_DAYS = 365
_RESOLVED_STATUSES = {"resolved"}


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def get_reason_categories(
    days: int = 90,
    session: Session = Depends(get_session),
    db: Session | None = None,
) -> dict[str, Any]:
    if isinstance(days, Session):
        days, session = (
            session if isinstance(session, int) else 90,
            days,
        )
    elif isinstance(session, int) and isinstance(days, Session):
        days, session = session, days

    if db is not None:
        session = db

    if days < 1 or days > _MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"days must be between 1 and {_MAX_DAYS}",
        )

    cutoff = _utc_now_naive() - timedelta(days=days)
    statement = select(
        McpScoreDispute.id,
        McpScoreDispute.reason_category,
        McpScoreDispute.status,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).where(McpScoreDispute.created_at >= cutoff)

    rows = session.execute(statement).all()

    reason_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    latencies: list[float] = []

    for row in rows:
        reason = row.reason_category
        reason = str(reason).strip() if reason is not None else ""
        reason_counts[reason or "unspecified"] += 1

        status = row.status
        status = str(status).strip() if status is not None else ""
        status = status or "unspecified"
        status_counts[status] += 1

        if (
            row.created_at is not None
            and row.resolved_at is not None
            and status.casefold() in _RESOLVED_STATUSES
        ):
            created_at = _as_utc(row.created_at)
            resolved_at = _as_utc(row.resolved_at)
            elapsed_hours = (resolved_at - created_at).total_seconds() / 3600.0
            if elapsed_hours >= 0:
                latencies.append(elapsed_hours)

    total_disputes = len(rows)
    resolved_disputes = sum(
        count
        for status, count in status_counts.items()
        if status.casefold() in _RESOLVED_STATUSES
    )
    open_disputes = total_disputes - resolved_disputes

    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    p90_latency = _percentile(latencies, 0.90)

    return {
        "total_disputes": total_disputes,
        "open_disputes": open_disputes,
        "resolved_disputes": resolved_disputes,
        "by_reason_category": [
            {"reason_category": reason, "count": count}
            for reason, count in sorted(reason_counts.items())
        ],
        "by_status": [
            {"status": status, "count": count}
            for status, count in sorted(status_counts.items())
        ],
        "resolution_latency_hours": {
            "mean": mean_latency,
            "p90": p90_latency,
            "samples": len(latencies),
        },
    }


get_reason_category_summary = get_reason_categories
get_reason_category_stats = get_reason_categories
get_reason_category_data = get_reason_categories
get_reason_category_breakdown = get_reason_categories
get_reason_category_metrics = get_reason_categories
get_reason_categories_logic = get_reason_categories
get_dispute_reason_categories = get_reason_categories
get_dispute_reason_category_summary = get_reason_categories
get_dispute_reason_category_stats = get_reason_categories
get_dispute_reason_category_logic = get_reason_categories
get_score_dispute_reason_categories = get_reason_categories
fetch_reason_categories = get_reason_categories


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    now = _utc_now_naive()
    disputes = [
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=50.0,
            proposed_axes={},
            reason_category="model_validation",
            explanation="First dispute",
            status="open",
            created_at=now,
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=60.0,
            proposed_axes={},
            reason_category="data_quality",
            explanation="Second dispute",
            status="resolved",
            admin_note="Reviewed",
            created_at=now,
            resolved_at=now + timedelta(hours=2),
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=70.0,
            proposed_axes={},
            reason_category="model_validation",
            explanation="Third dispute",
            status="open",
            created_at=now,
        ),
        McpScoreDispute(
            server_id=1,
            submitted_by=1,
            proposed_overall_risk=80.0,
            proposed_axes={},
            reason_category="data_quality",
            explanation="Fourth dispute",
            status="open",
            created_at=now,
        ),
    ]

    with SessionLocal.begin() as session:
        session.add_all(disputes)

    that_app = FastAPI()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/disputes/reason-categories")
    def reason_categories_endpoint(
        days: int = 90,
        session: Session = Depends(get_session),
    ):
        return get_reason_categories(days=days, session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/disputes/reason-categories", params={"days": 90})
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_disputes"] == 4
        assert payload["open_disputes"] + payload["resolved_disputes"] == 4
        assert len(payload["by_reason_category"]) == 2
        assert len(payload["by_status"]) == 2
        assert abs(payload["resolution_latency_hours"]["mean"] - 2.0) <= 0.01

    print("PASS")