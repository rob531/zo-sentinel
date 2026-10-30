from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute


AGE_BUCKETS = ("0-7d", "8-30d", "31-90d", "91+d")
MAX_DAYS = 365


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: float) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    return "91+d"


def get_dispute_aging(
    days: int = 30,
    session: Session = Depends(get_session),
    as_of: datetime | None = None,
) -> dict[str, Any]:
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"days must be between 1 and {MAX_DAYS}")

    reference = _utc_datetime(as_of if as_of is not None else utcnow())

    rows = session.query(
        McpScoreDispute.status,
        McpScoreDispute.reason_category,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).all()

    status_counts: dict[str, int] = {"open": 0, "resolved": 0}
    bucket_counts = {bucket: 0 for bucket in AGE_BUCKETS}
    reason_counts: dict[str, int] = {}
    resolution_hours: list[float] = []
    open_count = 0

    for row in rows:
        status = row.status
        status_counts[status] = status_counts.get(status, 0) + 1

        reason_category = row.reason_category if row.reason_category is not None else ""
        reason_counts[reason_category] = reason_counts.get(reason_category, 0) + 1

        if status != "resolved":
            open_count += 1
            age_days = max(
                0.0,
                (reference - _utc_datetime(row.created_at)).total_seconds() / 86400.0,
            )
            bucket_counts[_bucket_for_age(age_days)] += 1

        if status == "resolved" and row.resolved_at is not None:
            duration_hours = (
                _utc_datetime(row.resolved_at) - _utc_datetime(row.created_at)
            ).total_seconds() / 3600.0
            resolution_hours.append(duration_hours)

    median_resolution_hours = (
        float(median(resolution_hours)) if resolution_hours else None
    )
    reason_categories = [
        {"reason_category": reason_category, "count": count}
        for reason_category, count in sorted(
            reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return {
        "days": days,
        "open_count": open_count,
        "status_counts": status_counts,
        "age_buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in AGE_BUCKETS
        ],
        "median_resolution_hours": median_resolution_hours,
        "reason_categories": reason_categories,
    }


def get_score_dispute_age_trend(
    days: int = 30,
    session: Session = Depends(get_session),
    as_of: datetime | None = None,
) -> dict[str, Any]:
    return get_dispute_aging(days=days, session=session, as_of=as_of)


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    reference = datetime(2025, 1, 1, tzinfo=timezone.utc)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_score_disputes (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    submitted_by TEXT,
                    proposed_overall_risk TEXT,
                    proposed_axes TEXT,
                    reason_category TEXT,
                    status TEXT NOT NULL,
                    admin_note TEXT,
                    created_at DATETIME NOT NULL,
                    resolved_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_score_disputes (
                    id, server_id, submitted_by, proposed_overall_risk,
                    proposed_axes, reason_category, status, admin_note,
                    created_at, resolved_at
                ) VALUES (
                    :id, :server_id, :submitted_by, :proposed_overall_risk,
                    :proposed_axes, :reason_category, :status, :admin_note,
                    :created_at, :resolved_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "server_id": "server-1",
                    "submitted_by": "user-1",
                    "proposed_overall_risk": "high",
                    "proposed_axes": "[]",
                    "reason_category": "policy_interpretation",
                    "status": "open",
                    "admin_note": None,
                    "created_at": "2024-12-29 00:00:00.000000",
                    "resolved_at": None,
                },
                {
                    "id": 2,
                    "server_id": "server-2",
                    "submitted_by": "user-2",
                    "proposed_overall_risk": "medium",
                    "proposed_axes": "[]",
                    "reason_category": "policy_interpretation",
                    "status": "open",
                    "admin_note": None,
                    "created_at": "2024-11-17 00:00:00.000000",
                    "resolved_at": None,
                },
                {
                    "id": 3,
                    "server_id": "server-3",
                    "submitted_by": "user-3",
                    "proposed_overall_risk": "low",
                    "proposed_axes": "[]",
                    "reason_category": "policy_interpretation",
                    "status": "resolved",
                    "admin_note": None,
                    "created_at": "2024-12-22 00:00:00.000000",
                    "resolved_at": "2024-12-24 00:00:00.000000",
                },
                {
                    "id": 4,
                    "server_id": "server-4",
                    "submitted_by": "user-4",
                    "proposed_overall_risk": "high",
                    "proposed_axes": "[]",
                    "reason_category": "axis_ambiguity",
                    "status": "open",
                    "admin_note": None,
                    "created_at": "2024-09-03 00:00:00.000000",
                    "resolved_at": None,
                },
            ],
        )

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/disputes/aging")
    def disputes_aging(
        days: int = 30,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_dispute_aging(days=days, session=session, as_of=reference)

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/disputes/aging")
            assert response.status_code == 200
            payload = response.json()
            assert payload["days"] == 30
            assert payload["open_count"] == 3
            assert payload["status_counts"]["open"] == 3
            assert payload["status_counts"]["resolved"] == 1
            assert payload["median_resolution_hours"] == 48.0
            assert {"bucket": "0-7d", "count": 1} in payload["age_buckets"]
            assert {"bucket": "91+d", "count": 1} in payload["age_buckets"]
            assert {
                "reason_category": "axis_ambiguity",
                "count": 1,
            } in payload["reason_categories"]
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()