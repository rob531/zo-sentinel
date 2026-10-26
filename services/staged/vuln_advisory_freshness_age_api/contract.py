from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


class AdvisoryAgeBucket(BaseModel):
    bucket: str
    count: int


class AdvisorySeverityCount(BaseModel):
    severity: str
    count: int


class AdvisoryAgeResponse(BaseModel):
    days: int
    buckets: List[AdvisoryAgeBucket]
    by_severity: List[AdvisorySeverityCount]
    oldest_published_at: Optional[str]
    stale_count: int


router = APIRouter()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


@router.get("/vuln/advisory-age", response_model=AdvisoryAgeResponse)
def get_advisory_age(
    days: int = Query(30),
    ecosystem: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> AdvisoryAgeResponse:
    days = max(1, min(365, days))
    query = session.query(VulnAdvisory)
    if ecosystem is not None:
        query = query.filter(VulnAdvisory.ecosystem == ecosystem)

    advisories = query.all()
    now = datetime.now(timezone.utc)
    bucket_counts = Counter({"0-7d": 0, "8-30d": 0, "31-90d": 0, ">90d": 0})
    severity_counts: Counter = Counter()
    oldest_published_at: Optional[datetime] = None

    for advisory in advisories:
        published_at = advisory.published_at
        effective_at = published_at if published_at is not None else advisory.fetched_at
        if effective_at is None:
            continue

        age_days = max(0, int((now - _as_utc(effective_at)).total_seconds() // 86400))
        if age_days <= 7:
            bucket_counts["0-7d"] += 1
        elif age_days <= 30:
            bucket_counts["8-30d"] += 1
        elif age_days <= 90:
            bucket_counts["31-90d"] += 1
        else:
            bucket_counts[">90d"] += 1

        if advisory.severity is not None:
            severity_counts[advisory.severity] += 1

        if published_at is not None:
            normalized = _as_utc(published_at)
            if oldest_published_at is None or normalized < oldest_published_at:
                oldest_published_at = normalized

    return AdvisoryAgeResponse(
        days=days,
        buckets=[
            AdvisoryAgeBucket(bucket=bucket, count=bucket_counts[bucket])
            for bucket in ("0-7d", "8-30d", "31-90d", ">90d")
        ],
        by_severity=[
            AdvisorySeverityCount(severity=severity, count=count)
            for severity, count in sorted(severity_counts.items())
        ],
        oldest_published_at=(
            _isoformat_utc(oldest_published_at) if oldest_published_at is not None else None
        ),
        stale_count=bucket_counts[">90d"],
    )


def create_app() -> FastAPI:
    application = FastAPI(title="Vulnerability Advisory Freshness Age API")
    application.include_router(router, prefix="/api")
    return application


app = create_app()


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        for index, offset in enumerate((1, 10, 45, 200), start=1):
            published_at = now - timedelta(days=offset)
            session.add(
                VulnAdvisory(
                    id=f"test-advisory-{index}",
                    feed="test-feed",
                    summary=f"Test advisory {index}",
                    severity="high",
                    ecosystem="pypi",
                    package=f"test-package-{index}",
                    affected_ranges=[],
                    aliases=[],
                    source_url="https://example.test/advisory",
                    published_at=published_at,
                    fetched_at=published_at,
                    identities=[],
                    content_hash=f"test-content-hash-{index}",
                )
            )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/vuln/advisory-age")
            assert response.status_code == 200
            payload = response.json()
            assert len(payload["buckets"]) == 4
            bucket_counts = {item["bucket"]: item["count"] for item in payload["buckets"]}
            assert bucket_counts["0-7d"] == 1
            assert bucket_counts[">90d"] == 1
            assert payload["stale_count"] == 1
    finally:
        that_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()