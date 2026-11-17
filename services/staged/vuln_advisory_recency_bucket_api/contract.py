from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory


BUCKET_LABELS = (
    "0-7d",
    "8-30d",
    "31-90d",
    "91-180d",
    "181-365d",
    "365d+",
)


class TopEcosystem(BaseModel):
    ecosystem: str
    count: int


class AdvisoryBucket(BaseModel):
    bucket: str
    total: int
    by_severity: Dict[str, int]
    top_ecosystems: List[TopEcosystem]


class AdvisoryRecencyResponse(BaseModel):
    buckets: int
    generated_at: str
    series: List[AdvisoryBucket]
    totals: Dict[str, int]


router = APIRouter()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7d"
    if age_days <= 30:
        return "8-30d"
    if age_days <= 90:
        return "31-90d"
    if age_days <= 180:
        return "91-180d"
    if age_days <= 365:
        return "181-365d"
    return "365d+"


@router.get(
    "/vuln/advisory-recency",
    response_model=AdvisoryRecencyResponse,
)
def advisory_recency(
    buckets: int = Query(default=6),
    session=Depends(get_session),
) -> AdvisoryRecencyResponse:
    requested_buckets = max(1, min(12, buckets))
    now = datetime.now(timezone.utc)

    rows = session.execute(
        select(
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.published_at,
        )
    ).all()

    grouped = {}
    total_advisories = 0

    for severity, ecosystem, published_at in rows:
        if published_at is None:
            continue

        total_advisories += 1
        age_days = max(0, (now - _as_utc(published_at)).days)
        bucket = _bucket_for_age(age_days)

        bucket_data = grouped.setdefault(
            bucket,
            {
                "total": 0,
                "severity": Counter(),
                "ecosystem": Counter(),
            },
        )
        bucket_data["total"] += 1

        severity_key = severity.strip() if severity and severity.strip() else "UNKNOWN"
        bucket_data["severity"][severity_key] += 1

        if ecosystem and ecosystem.strip():
            bucket_data["ecosystem"][ecosystem.strip()] += 1

    ordered_buckets = [label for label in BUCKET_LABELS if label in grouped]
    ordered_buckets = ordered_buckets[:requested_buckets]

    series = []
    for label in ordered_buckets:
        bucket_data = grouped[label]
        severity_counts = bucket_data["severity"]
        ecosystem_counts = bucket_data["ecosystem"]

        top_ecosystems = [
            TopEcosystem(ecosystem=ecosystem, count=count)
            for ecosystem, count in sorted(
                ecosystem_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:3]
        ]

        series.append(
            AdvisoryBucket(
                bucket=label,
                total=bucket_data["total"],
                by_severity=dict(sorted(severity_counts.items())),
                top_ecosystems=top_ecosystems,
            )
        )

    return AdvisoryRecencyResponse(
        buckets=requested_buckets,
        generated_at=now.isoformat(),
        series=series,
        totals={"advisories": total_advisories},
    )


app = FastAPI(title="vuln_advisory_recency_bucket_api")
app.include_router(router, prefix="/api")


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_advisories (
                id VARCHAR PRIMARY KEY,
                feed VARCHAR,
                severity VARCHAR,
                ecosystem VARCHAR,
                published_at DATETIME,
                fetched_at DATETIME,
                package VARCHAR
            )
            """
        )

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.execute(
            insert(VulnAdvisory.__table__),
            [
                {
                    "id": "advisory-1",
                    "feed": "osv",
                    "severity": "HIGH",
                    "ecosystem": "npm",
                    "published_at": now - timedelta(days=3),
                    "fetched_at": now,
                    "package": "left-pad",
                },
                {
                    "id": "advisory-2",
                    "feed": "osv",
                    "severity": None,
                    "ecosystem": "npm",
                    "published_at": now - timedelta(days=3),
                    "fetched_at": now,
                    "package": "right-pad",
                },
                {
                    "id": "advisory-3",
                    "feed": "github",
                    "severity": "MEDIUM",
                    "ecosystem": "pypi",
                    "published_at": now - timedelta(days=15),
                    "fetched_at": now,
                    "package": "requests",
                },
                {
                    "id": "advisory-4",
                    "feed": "github",
                    "severity": "LOW",
                    "ecosystem": "cargo",
                    "published_at": now - timedelta(days=400),
                    "fetched_at": now,
                    "package": "serde",
                },
            ],
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisory-recency")
        assert response.status_code == 200
        payload = response.json()

    assert payload["buckets"] == 6
    assert len(payload["series"]) == 3
    assert payload["totals"]["advisories"] == 4

    first_bucket = payload["series"][0]
    assert first_bucket["bucket"] == "0-7d"
    assert first_bucket["total"] == 2
    assert first_bucket["by_severity"]["UNKNOWN"] == 1
    assert first_bucket["top_ecosystems"][0] == {"ecosystem": "npm", "count": 2}

    print("PASS")


if __name__ == "__main__":
    _run_self_test()