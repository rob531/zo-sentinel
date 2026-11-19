"""logic.py -- the service's data/computation layer (one concern, one file).

Queries the real users table through the application's SQLAlchemy models and
computes account-age cohorts for a trailing time window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User


COHORT_BUCKETS = ("0-7", "8-30", "31-90", "91-365", "365+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 365:
        return "91-365"
    return "365+"


def get_age_cohorts(db: Session, days: int) -> Dict[str, Any]:
    """Return age-cohort, role, and organization counts for recent users.

    The cutoff is inclusive and parameterized by SQLAlchemy. Account age is
    calculated in UTC so the behavior is consistent across database engines.
    """
    if days < 0:
        raise ValueError("days must be non-negative")

    now = _utc_now()
    cutoff = (now - timedelta(days=days)).replace(tzinfo=None)
    rows = db.execute(
        select(User.created_at, User.org_id, User.role).where(
            User.created_at >= cutoff
        )
    ).all()

    cohort_counts = {bucket: 0 for bucket in COHORT_BUCKETS}
    role_counts: Dict[str, int] = {}
    org_counts: Dict[str, int] = {}

    for created_at, org_id, role in rows:
        age_days = max(0, (now - _as_utc(created_at)).days)
        bucket = _bucket_for_age(age_days)
        cohort_counts[bucket] += 1

        role_name = role or "unknown"
        role_counts[role_name] = role_counts.get(role_name, 0) + 1

        org_name = org_id or "unknown"
        org_counts[org_name] = org_counts.get(org_name, 0) + 1

    return {
        "days": days,
        "cohorts": [
            {"bucket": bucket, "count": cohort_counts[bucket]}
            for bucket in COHORT_BUCKETS
        ],
        "total": len(rows),
        "by_role": [
            {"role": role, "count": count}
            for role, count in sorted(role_counts.items())
        ],
        "by_org": [
            {"org_id": org_id, "count": count}
            for org_id, count in sorted(org_counts.items())
        ],
    }


def get_user_age_cohorts(db: Session, days: int) -> Dict[str, Any]:
    """Compatibility name for the account-age cohort summary."""
    return get_age_cohorts(db, days)


def calculate_age_cohorts(db: Session, days: int) -> Dict[str, Any]:
    """Compatibility name for the account-age cohort summary."""
    return get_age_cohorts(db, days)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/users/age-cohort")
    def age_cohort_route(days: int = 500, db: Session = Depends(get_session)):
        return get_age_cohorts(db, days)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ages = (1, 5, 10, 25, 60, 120, 200, 400)
    roles = ("admin", "member", "viewer", "admin")
    with TestingSession() as session:
        session.add_all(
            [
                User(
                    id=f"cohort-user-{index}",
                    email=f"cohort-user-{index}@example.com",
                    org_id=f"org-{index % 3}",
                    role=roles[index % len(roles)],
                    password_hash="test-only",
                    created_at=now - timedelta(days=age, hours=1),
                )
                for index, age in enumerate(ages)
            ]
        )
        session.commit()

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/users/age-cohort?days=500")
    payload = response.json()
    assert response.status_code == 200
    assert "cohorts" in payload
    assert [item["bucket"] for item in payload["cohorts"]] == list(COHORT_BUCKETS)
    assert sum(item["count"] for item in payload["cohorts"]) == len(ages)
    assert payload["total"] == len(ages)
    assert {item["role"] for item in payload["by_role"]} == set(roles)

    print("PASS")