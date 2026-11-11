from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BUCKET_LABELS = ("0-7d", "8-30d", "31-90d", "91+d")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return _BUCKET_LABELS[0]
    if age_days <= 30:
        return _BUCKET_LABELS[1]
    if age_days <= 90:
        return _BUCKET_LABELS[2]
    return _BUCKET_LABELS[3]


def build_verdict_age_buckets(
    session: Session,
    days: int = 30,
    max_age_days: int = 30,
) -> dict[str, Any]:
    if not 1 <= int(days) <= 365:
        raise ValueError("days must be between 1 and 365")
    if int(max_age_days) < 0:
        raise ValueError("max_age_days must be non-negative")

    days = int(days)
    max_age_days = int(max_age_days)
    now = _utc_now()
    since = now - timedelta(days=days)

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_seen,
        ).where(
            McpServerRegistry.last_assessed.is_not(None),
            McpServerRegistry.last_assessed >= since,
        )
    ).all()

    unassessed_total = session.execute(
        select(func.count())
        .select_from(McpServerRegistry)
        .where(McpServerRegistry.last_assessed.is_(None))
    ).scalar_one()

    bucket_counts = {label: 0 for label in _BUCKET_LABELS}
    bucket_tiers: dict[str, dict[str, int]] = {
        label: {} for label in _BUCKET_LABELS
    }
    assessed_total = 0
    stale_total = 0

    for row in rows:
        last_assessed = row.last_assessed
        if last_assessed is None:
            continue

        assessed_total += 1
        age_days = max(0, (_utc_now() - _as_utc(last_assessed)).days)
        bucket = _bucket_for_age(age_days)
        bucket_counts[bucket] += 1

        verdict = row.verdict
        if verdict is not None:
            verdict_key = str(verdict)
            bucket_tiers[bucket][verdict_key] = (
                bucket_tiers[bucket].get(verdict_key, 0) + 1
            )

        if age_days > max_age_days:
            stale_total += 1

    buckets = [
        {
            "bucket": label,
            "count": bucket_counts[label],
            "by_tier": bucket_tiers[label],
        }
        for label in _BUCKET_LABELS
    ]

    return {
        "days": days,
        "max_age_days": max_age_days,
        "buckets": buckets,
        "stale_total": stale_total,
        "assessed_total": assessed_total,
        "unassessed_total": int(unassessed_total),
        "generated_at": now.isoformat(),
    }


def get_verdict_age_buckets(
    session: Session,
    days: int = 30,
    max_age_days: int = 30,
) -> dict[str, Any]:
    return build_verdict_age_buckets(session, days, max_age_days)


def compute_verdict_age_buckets(
    session: Session,
    days: int = 30,
    max_age_days: int = 30,
) -> dict[str, Any]:
    return build_verdict_age_buckets(session, days, max_age_days)


def verdict_age_buckets(
    session: Session,
    days: int = 30,
    max_age_days: int = 30,
) -> dict[str, Any]:
    return build_verdict_age_buckets(session, days, max_age_days)


def _run_self_test() -> None:
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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    now = _utc_now()
    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="self-test-today",
                    verdict="TRUSTED_GENERAL",
                    risk_tier="GENERAL",
                    last_assessed=now,
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="self-test-ten-days",
                    verdict="CONDITIONAL",
                    risk_tier="GENERAL",
                    last_assessed=now - timedelta(days=10),
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="self-test-old",
                    verdict="MALICIOUS",
                    risk_tier="HIGH",
                    last_assessed=now - timedelta(days=200),
                    last_seen=now,
                ),
                McpServerRegistry(
                    server_id="self-test-unassessed",
                    verdict="INSUFFICIENT",
                    risk_tier="GENERAL",
                    last_assessed=None,
                    last_seen=now,
                ),
            ]
        )
        session.commit()

    that_app = FastAPI()

    def override_session():
        with TestingSession() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_session

    @that_app.get("/api/verdict/age-buckets")
    def age_buckets(
        days: int = 30,
        max_age_days: int = 30,
        session: Session = Depends(get_session),
    ):
        return build_verdict_age_buckets(session, days, max_age_days)

    with TestClient(that_app) as client:
        response = client.get(
            "/api/verdict/age-buckets",
            params={"days": 365, "max_age_days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert {
            "days",
            "max_age_days",
            "buckets",
            "stale_total",
            "assessed_total",
            "unassessed_total",
            "generated_at",
        }.issubset(payload)
        assert sum(item["count"] for item in payload["buckets"]) == payload[
            "assessed_total"
        ]
        assert payload["unassessed_total"] == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()