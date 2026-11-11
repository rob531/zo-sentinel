from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import bucket_age

router = APIRouter(prefix="/api")

_BUCKET_BOUNDS: tuple[tuple[str, int, int | None], ...] = (
    ("0_7d", 0, 7),
    ("8_30d", 8, 30),
    ("31_90d", 31, 90),
    ("91_180d", 91, 180),
    ("181_365d", 181, 365),
    ("365d_plus", 365, None),
)

_SEVERITIES = ("critical", "high", "medium", "low", "unknown")


class AdvisoryAgeBucket(BaseModel):
    bucket: str
    min_days: int
    max_days: int | None
    count: int = Field(ge=0)


class AdvisoryAgeBucketsResponse(BaseModel):
    buckets: list[AdvisoryAgeBucket]
    by_severity: dict[str, int]
    total: int = Field(ge=0)
    stale_90d: int = Field(ge=0)
    computed_at: datetime


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _aggregate_age_buckets(
    advisories: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    counts = {name: 0 for name, _, _ in _BUCKET_BOUNDS}
    by_severity = {severity: 0 for severity in _SEVERITIES}
    stale_90d = 0

    for advisory in advisories:
        published_at = advisory.get("published_at")
        if published_at is None:
            continue

        age_days = max((now - _as_utc(published_at)).days, 0)
        counts[bucket_age(age_days)] += 1

        severity = str(advisory.get("severity") or "unknown").strip().lower()
        if severity not in by_severity:
            severity = "unknown"
        by_severity[severity] += 1

        if age_days > 90:
            stale_90d += 1

    buckets = [
        {
            "bucket": name,
            "min_days": min_days,
            "max_days": max_days,
            "count": counts[name],
        }
        for name, min_days, max_days in _BUCKET_BOUNDS
    ]

    return {
        "buckets": buckets,
        "by_severity": by_severity,
        "total": sum(by_severity.values()),
        "stale_90d": stale_90d,
        "computed_at": now.isoformat(),
    }


@router.get(
    "/advisories/age-buckets",
    response_model=AdvisoryAgeBucketsResponse,
)
def get_advisory_age_buckets(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.published_at,
            VulnAdvisory.fetched_at,
            VulnAdvisory.feed,
        ).where(VulnAdvisory.published_at.is_not(None))
    ).all()

    advisories = [
        {
            "id": row.id,
            "severity": row.severity,
            "ecosystem": row.ecosystem,
            "published_at": row.published_at,
            "fetched_at": row.fetched_at,
            "feed": row.feed,
        }
        for row in rows
    ]
    return _aggregate_age_buckets(advisories, now)


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    severity TEXT,
                    ecosystem TEXT,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP,
                    feed TEXT
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        severities = ("critical", "high", "medium", "low")
        for index, (offset, severity) in enumerate(
            zip((0, 10, 45, 400), severities),
            start=1,
        ):
            published_at = now - timedelta(days=offset)
            connection.execute(
                text(
                    """
                    INSERT INTO vuln_advisories
                        (id, severity, ecosystem, published_at, fetched_at, feed)
                    VALUES
                        (:id, :severity, :ecosystem, :published_at, :fetched_at, :feed)
                    """
                ),
                {
                    "id": index,
                    "severity": severity,
                    "ecosystem": "pypi",
                    "published_at": published_at,
                    "fetched_at": now,
                    "feed": "test",
                },
            )

    test_session_factory = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
    )

    def get_test_session() -> Session:
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    response = TestClient(test_app).get("/api/advisories/age-buckets")
    response.raise_for_status()
    payload = response.json()

    bucket_counts = {
        item["bucket"]: item["count"] for item in payload["buckets"]
    }
    assert bucket_counts["0_7d"] == 1
    assert bucket_counts["8_30d"] == 1
    assert bucket_counts["31_90d"] == 1
    assert bucket_counts["365d_plus"] == 1
    assert payload["total"] == 4
    assert sum(payload["by_severity"].values()) == payload["total"]

    print("PASS")