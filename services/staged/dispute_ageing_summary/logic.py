"""Logic for scoring dispute ageing summaries."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpScoreDispute


_TERMINAL_STATUSES = {"resolved", "rejected", "closed"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> float:
    return (_as_utc(now) - _as_utc(created_at)).total_seconds() / 86400.0


def compute_dispute_ageing(
    db: Session,
    days: int = 90,
    now: Optional[datetime] = None,
) -> Dict[str, object]:
    """Compute the ageing summary for score disputes created in the lookback window."""
    window_days = max(0, int(days))
    reference_time = _as_utc(now or datetime.now(timezone.utc))
    cutoff = reference_time - timedelta(days=window_days)

    rows = db.execute(
        select(
            McpScoreDispute.created_at,
            McpScoreDispute.status,
            McpScoreDispute.reason_category,
        )
    ).all()

    open_ages: List[float] = []
    reason_ages: Dict[str, List[float]] = {}
    status_counts: Dict[str, int] = {}
    resolved_count = 0

    for created_at, status, reason_category in rows:
        if created_at is None:
            continue

        created = _as_utc(created_at)
        age = _age_days(created_at, reference_time)
        if age < 0 or created < cutoff:
            continue

        normalized_status = (status or "").strip().lower() or "unknown"
        status_counts[normalized_status] = status_counts.get(normalized_status, 0) + 1

        if normalized_status == "resolved":
            resolved_count += 1

        if normalized_status not in _TERMINAL_STATUSES:
            open_ages.append(age)
            reason = reason_category or "unknown"
            reason_ages.setdefault(reason, []).append(age)

    open_ages.sort()
    if open_ages:
        middle = len(open_ages) // 2
        if len(open_ages) % 2:
            median_open_days: Optional[float] = float(open_ages[middle])
        else:
            median_open_days = (
                open_ages[middle - 1] + open_ages[middle]
            ) / 2.0
        oldest_open_days: Optional[int] = int(max(open_ages))
    else:
        median_open_days = None
        oldest_open_days = None

    by_reason = [
        {
            "reason_category": reason,
            "open_count": len(ages),
            "avg_age_days": float(sum(ages) / len(ages)),
        }
        for reason, ages in sorted(reason_ages.items())
    ]

    return {
        "window_days": window_days,
        "open_count": len(open_ages),
        "resolved_count": resolved_count,
        "oldest_open_days": oldest_open_days,
        "median_open_days": median_open_days,
        "by_reason": by_reason,
        "status_breakdown": {
            status: status_counts[status] for status in sorted(status_counts)
        },
    }


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    McpScoreDispute.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    fixed_now = datetime(2025, 1, 10, 12, 0, 0, tzinfo=timezone.utc)
    session = SessionLocal()
    try:
        session.add_all(
            [
                McpScoreDispute(
                    server_id="server-1",
                    submitted_by="analyst-1",
                    proposed_overall_risk=0.8,
                    proposed_axes={"factual": 0.9},
                    reason_category="factual_error",
                    explanation="Incorrect factual score",
                    status="OPEN",
                    created_at=fixed_now - timedelta(days=10),
                ),
                McpScoreDispute(
                    server_id="server-2",
                    submitted_by="analyst-2",
                    proposed_overall_risk=0.6,
                    proposed_axes={"factual": 0.7},
                    reason_category="factual_error",
                    explanation="Incorrect factual score",
                    status="open",
                    created_at=fixed_now - timedelta(days=20),
                ),
                McpScoreDispute(
                    server_id="server-3",
                    submitted_by="analyst-3",
                    proposed_overall_risk=0.4,
                    proposed_axes={"model": 0.5},
                    reason_category="model_error",
                    explanation="Model disagreement",
                    status="Open",
                    created_at=fixed_now - timedelta(days=5),
                ),
                McpScoreDispute(
                    server_id="server-4",
                    submitted_by="analyst-4",
                    proposed_overall_risk=0.3,
                    proposed_axes={},
                    reason_category="factual_error",
                    explanation="Resolved dispute",
                    status="RESOLVED",
                    created_at=fixed_now - timedelta(days=3),
                    resolved_at=fixed_now,
                ),
            ]
        )
        session.commit()

        result = compute_dispute_ageing(session, days=90, now=fixed_now)
        assert result["open_count"] == 3
        assert result["resolved_count"] == 1
        assert result["oldest_open_days"] == 20
        assert result["median_open_days"] == 10.0
        assert any(
            item["reason_category"] == "factual_error" and item["open_count"] == 2
            for item in result["by_reason"]
        )
        print("PASS")
    finally:
        session.close()
        engine.dispose()