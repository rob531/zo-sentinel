from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


def _utc_naive_now() -> datetime:
    return datetime.utcnow()


def _as_utc_naive(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _latency_hours(created_at: Any, resolved_at: Any) -> float | None:
    created = _as_utc_naive(created_at)
    resolved = _as_utc_naive(resolved_at)
    if created is None or resolved is None:
        return None
    return max(0.0, (resolved - created).total_seconds() / 3600.0)


def _is_open(status: Any, resolved_at: Any) -> bool:
    return str(status or "").lower() != "resolved" and resolved_at is None


def get_dispute_resolution_sla(
    days: int = 30,
    db: Session = Depends(get_session),
    session: Session | None = None,
) -> dict[str, Any]:
    if session is not None:
        db = session
    if isinstance(days, Session):
        db, days = days, db if isinstance(db, int) else 30

    days = max(1, int(days))
    cutoff = _utc_naive_now() - timedelta(days=days)

    statement = select(
        McpScoreDispute.id,
        McpScoreDispute.reason_category,
        McpScoreDispute.status,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).where(McpScoreDispute.created_at >= cutoff)

    rows = db.execute(statement).all()
    categories: dict[str, dict[str, int]] = {}
    overall = {
        "under_24h": 0,
        "d1_to_7d": 0,
        "over_7d": 0,
    }
    latencies: list[float] = []
    open_count = 0

    for row in rows:
        reason_category = str(row.reason_category or "unknown")
        bucket = categories.setdefault(
            reason_category,
            {
                "reason_category": reason_category,
                "open": 0,
                "resolved": 0,
                "under_24h": 0,
                "d1_to_7d": 0,
                "over_7d": 0,
            },
        )

        if _is_open(row.status, row.resolved_at):
            open_count += 1
            bucket["open"] += 1
            continue

        bucket["resolved"] += 1
        hours = _latency_hours(row.created_at, row.resolved_at)
        if hours is None:
            continue

        latencies.append(hours)
        if hours < 24:
            bucket["under_24h"] += 1
            overall["under_24h"] += 1
        elif hours <= 7 * 24:
            bucket["d1_to_7d"] += 1
            overall["d1_to_7d"] += 1
        else:
            bucket["over_7d"] += 1
            overall["over_7d"] += 1

    return {
        "days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(rows),
        "open_count": open_count,
        "resolved_count": len(rows) - open_count,
        "median_hours": float(median(latencies)) if latencies else 0.0,
        "buckets": [categories[key] for key in sorted(categories)],
        "overall": overall,
    }


def build_dispute_resolution_sla(
    days: int = 30,
    db: Session = Depends(get_session),
    session: Session | None = None,
) -> dict[str, Any]:
    return get_dispute_resolution_sla(days=days, db=db, session=session)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)

    with Session(engine) as session:
        now = _utc_naive_now()
        session.add_all(
            [
                McpScoreDispute(
                    id=1,
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    reason_category="accuracy",
                    status="resolved",
                    admin_note="done",
                    explanation="seed",
                    created_at=now - timedelta(hours=3),
                    resolved_at=now - timedelta(hours=1),
                ),
                McpScoreDispute(
                    id=2,
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    reason_category="coverage",
                    status="resolved",
                    admin_note="done",
                    explanation="seed",
                    created_at=now - timedelta(days=6),
                    resolved_at=now - timedelta(days=1),
                ),
                McpScoreDispute(
                    id=3,
                    server_id=1,
                    submitted_by=1,
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    reason_category="accuracy",
                    status="open",
                    admin_note=None,
                    explanation="seed",
                    created_at=now - timedelta(hours=2),
                    resolved_at=None,
                ),
            ]
        )
        session.commit()

    testing_app = FastAPI()

    def override_get_session():
        with Session(engine) as session:
            yield session

    testing_app.dependency_overrides[get_session] = override_get_session

    @testing_app.get("/api/disputes/sla")
    def disputes_sla(days: int = 30, db: Session = Depends(get_session)):
        return get_dispute_resolution_sla(days=days, db=db)

    response = TestClient(testing_app).get("/api/disputes/sla", params={"days": 30})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["open_count"] == 1
    assert payload["median_hours"] > 0
    assert payload["overall"]["under_24h"] == 1
    assert any(
        bucket["reason_category"] == "accuracy" and bucket["under_24h"] == 1
        for bucket in payload["buckets"]
    )
    print("PASS")