from __future__ import annotations

from math import floor
from typing import Sequence

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percentile
    lower = floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _resolution_age_expression(session: Session):
    if session.get_bind().dialect.name == "sqlite":
        return (
            func.julianday(McpScoreDispute.resolved_at)
            - func.julianday(McpScoreDispute.created_at)
        ) * 24.0

    return (
        cast(
            func.extract(
                "epoch",
                McpScoreDispute.resolved_at - McpScoreDispute.created_at,
            ),
            Float,
        )
        / 3600.0
    )


def get_dispute_resolution_histogram(
    session: Session,
    buckets: int = 6,
    bucket_count: int | None = None,
) -> dict:
    count = bucket_count if bucket_count is not None else buckets
    if count < 1:
        raise ValueError("buckets must be at least 1")

    age_expression = _resolution_age_expression(session)
    resolved_rows = session.execute(
        select(age_expression.label("age_hours")).where(
            McpScoreDispute.resolved_at.is_not(None)
        )
    ).scalars()
    ages = [float(age) for age in resolved_rows if age is not None]
    ages.sort()

    unresolved_count = int(
        session.execute(
            select(func.count())
            .select_from(McpScoreDispute)
            .where(McpScoreDispute.resolved_at.is_(None))
        ).scalar_one()
    )

    max_age = max(ages) if ages else 0.0
    if max_age < 0:
        max_age = 0.0
    width = max_age / count if max_age else 0.0

    bucket_counts = [0] * count
    for age in ages:
        if width:
            index = min(int(age / width), count - 1)
        else:
            index = 0
        bucket_counts[index] += 1

    bucket_rows = []
    for index, bucket_total in enumerate(bucket_counts):
        lo_hours = index * width
        hi_hours = max_age if index == count - 1 else (index + 1) * width
        bucket_rows.append(
            {
                "index": index,
                "lo_hours": float(lo_hours),
                "hi_hours": float(hi_hours),
                "count": bucket_total,
            }
        )

    return {
        "bucket_count": count,
        "buckets": bucket_rows,
        "total_resolved": len(ages),
        "unresolved_count": unresolved_count,
        "median_hours": _percentile(ages, 0.5),
        "p90_hours": _percentile(ages, 0.9),
    }


get_dispute_resolution_time_histogram = get_dispute_resolution_histogram
get_resolution_time_histogram = get_dispute_resolution_histogram
get_resolution_latency_histogram = get_dispute_resolution_histogram
get_resolution_histogram = get_dispute_resolution_histogram
get_histogram = get_dispute_resolution_histogram
get_age_histogram = get_dispute_resolution_histogram
compute_dispute_resolution_histogram = get_dispute_resolution_histogram
compute_resolution_histogram = get_dispute_resolution_histogram


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import Depends, FastAPI
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/resolution-latency")
    def resolution_latency(
        buckets: int = 6,
        session: Session = Depends(get_session),
    ):
        return get_dispute_resolution_histogram(session, buckets)

    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with TestSession() as session:
        for hours, status in (
            (1, "resolved"),
            (3, "resolved"),
            (5, "resolved"),
            (None, "open"),
            (None, "open"),
        ):
            session.add(
                McpScoreDispute(
                    server_id="server-test",
                    submitted_by="user-test",
                    proposed_overall_risk=0.5,
                    reason_category="test",
                    explanation="Acceptance fixture",
                    status=status,
                    created_at=now,
                    resolved_at=now + timedelta(hours=hours) if hours else None,
                )
            )
        session.commit()

    response = TestClient(test_app).get(
        "/api/disputes/resolution-latency",
        params={"buckets": 6},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_resolved"] == 3
    assert payload["unresolved_count"] == 2
    assert payload["median_hours"] == 3.0
    print("PASS")