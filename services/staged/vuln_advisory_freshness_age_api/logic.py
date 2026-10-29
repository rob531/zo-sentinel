from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/api", tags=["vuln_advisory_freshness_age_api"])

BUCKET_NAMES = ("0-7d", "8-30d", "31-90d", ">90d")
SECONDS_PER_DAY = 24 * 60 * 60


class AdvisoryAgeBucket(BaseModel):
    bucket: str
    count: int


class AdvisorySeverityCount(BaseModel):
    severity: str
    count: int


class AdvisoryAgeResponse(BaseModel):
    days: int
    buckets: list[AdvisoryAgeBucket]
    by_severity: list[AdvisorySeverityCount]
    oldest_published_at: Optional[str]
    stale_count: int


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_utc(value: Optional[datetime]) -> Optional[str]:
    normalized = _as_utc(value)
    if normalized is None:
        return None
    return normalized.isoformat().replace("+00:00", "Z")


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return BUCKET_NAMES[0]
    if age_days <= 30:
        return BUCKET_NAMES[1]
    if age_days <= 90:
        return BUCKET_NAMES[2]
    return BUCKET_NAMES[3]


@router.get("/vuln/advisory-age", response_model=AdvisoryAgeResponse)
def get_advisory_age(
    days: int = Query(default=30),
    ecosystem: Optional[str] = Query(default=None),
    session=Depends(get_session),
) -> AdvisoryAgeResponse:
    clamped_days = max(1, min(365, days))
    query = session.query(
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
    )
    if ecosystem is not None:
        query = query.filter(VulnAdvisory.ecosystem == ecosystem)

    now = datetime.now(timezone.utc)
    bucket_counts = {name: 0 for name in BUCKET_NAMES}
    severity_counts: dict[str, int] = {}
    oldest_published_at: Optional[datetime] = None

    for severity, _row_ecosystem, published_at, fetched_at in query.all():
        effective_published_at = _as_utc(published_at) or _as_utc(fetched_at)
        if effective_published_at is None:
            age_days = 0
        else:
            age_days = max(
                0, int((now - effective_published_at).total_seconds() // SECONDS_PER_DAY)
            )
            if (
                oldest_published_at is None
                or effective_published_at < oldest_published_at
            ):
                oldest_published_at = effective_published_at

        bucket_counts[_bucket_for_age(age_days)] += 1
        severity_name = severity or "unknown"
        severity_counts[severity_name] = severity_counts.get(severity_name, 0) + 1

    return AdvisoryAgeResponse(
        days=clamped_days,
        buckets=[
            AdvisoryAgeBucket(bucket=name, count=bucket_counts[name])
            for name in BUCKET_NAMES
        ],
        by_severity=[
            AdvisorySeverityCount(severity=name, count=count)
            for name, count in sorted(severity_counts.items())
        ],
        oldest_published_at=_format_utc(oldest_published_at),
        stale_count=bucket_counts[BUCKET_NAMES[-1]],
    )


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
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    published_at DATETIME,
                    fetched_at DATETIME
                )
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for row_id, offset in enumerate((1, 10, 45, 200), start=1):
            connection.execute(
                text(
                    """
                    INSERT INTO vuln_advisories
                        (id, severity, ecosystem, published_at, fetched_at)
                    VALUES
                        (:id, :severity, :ecosystem, :published_at, :fetched_at)
                    """
                ),
                {
                    "id": row_id,
                    "severity": "critical" if row_id == 1 else "high",
                    "ecosystem": "npm",
                    "published_at": now - timedelta(days=offset),
                    "fetched_at": now - timedelta(days=offset),
                },
            )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisory-age")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["buckets"]) == 4
        counts = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert counts["0-7d"] == 1
        assert counts[">90d"] == 1
        assert payload["stale_count"] == 1

    print("PASS")