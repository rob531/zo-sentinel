from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api")


class PublishLatencyPoint(BaseModel):
    date: str
    advisory_count: int
    mean_latency_hours: float
    max_latency_hours: float


class WorstPublishLatency(BaseModel):
    advisory_id: Union[int, str]
    feed: Optional[str]
    latency_hours: float


class PublishLatencyResponse(BaseModel):
    days: int
    feed: Optional[str]
    series: List[PublishLatencyPoint]
    worst: Optional[WorstPublishLatency]


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _query_postgres(
    db: Session,
    days: int,
    feed: Optional[str],
) -> PublishLatencyResponse:
    parameters = {"days": days, "feed": feed}
    series_rows = db.execute(
        text(
            """
            SELECT
                published_at::date AS bucket_date,
                COUNT(*) AS advisory_count,
                AVG(
                    GREATEST(
                        EXTRACT(EPOCH FROM (fetched_at - published_at)) / 3600.0,
                        0
                    )
                ) AS mean_latency_hours,
                MAX(
                    GREATEST(
                        EXTRACT(EPOCH FROM (fetched_at - published_at)) / 3600.0,
                        0
                    )
                ) AS max_latency_hours
            FROM vuln_advisories
            WHERE published_at >= CURRENT_TIMESTAMP
                  - (CAST(:days AS INTEGER) * INTERVAL '1 day')
              AND (:feed IS NULL OR feed = :feed)
            GROUP BY published_at::date
            ORDER BY published_at::date ASC
            """
        ),
        parameters,
    ).mappings()

    series = [
        PublishLatencyPoint(
            date=row["bucket_date"].isoformat(),
            advisory_count=int(row["advisory_count"]),
            mean_latency_hours=float(row["mean_latency_hours"]),
            max_latency_hours=float(row["max_latency_hours"]),
        )
        for row in series_rows
    ]

    worst_row = db.execute(
        text(
            """
            WITH filtered AS (
                SELECT
                    id,
                    feed,
                    GREATEST(
                        EXTRACT(EPOCH FROM (fetched_at - published_at)) / 3600.0,
                        0
                    ) AS latency_hours
                FROM vuln_advisories
                WHERE published_at >= CURRENT_TIMESTAMP
                      - (CAST(:days AS INTEGER) * INTERVAL '1 day')
                  AND (:feed IS NULL OR feed = :feed)
            )
            SELECT id, feed, latency_hours
            FROM filtered
            ORDER BY latency_hours DESC, id ASC
            LIMIT 1
            """
        ),
        parameters,
    ).mappings().first()

    worst = None
    if worst_row is not None:
        worst = WorstPublishLatency(
            advisory_id=worst_row["id"],
            feed=worst_row["feed"],
            latency_hours=float(worst_row["latency_hours"]),
        )

    return PublishLatencyResponse(
        days=days,
        feed=feed,
        series=series,
        worst=worst,
    )


def _query_sqlite(
    db: Session,
    days: int,
    feed: Optional[str],
) -> PublishLatencyResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    statement = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.published_at,
        VulnAdvisory.fetched_at,
    ).where(VulnAdvisory.published_at >= cutoff)

    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)

    rows = db.execute(statement).mappings().all()
    buckets: Dict[date, List[float]] = {}

    for row in rows:
        published_at = _utc_datetime(row["published_at"])
        fetched_at = _utc_datetime(row["fetched_at"])
        latency_hours = max(
            (fetched_at - published_at).total_seconds() / 3600.0,
            0.0,
        )
        buckets.setdefault(published_at.date(), []).append(latency_hours)

    series = [
        PublishLatencyPoint(
            date=bucket_date.isoformat(),
            advisory_count=len(latencies),
            mean_latency_hours=sum(latencies) / len(latencies),
            max_latency_hours=max(latencies),
        )
        for bucket_date, latencies in sorted(buckets.items())
    ]

    worst = None
    if rows:
        worst_row = max(
            rows,
            key=lambda row: (
                max(
                    (
                        _utc_datetime(row["fetched_at"])
                        - _utc_datetime(row["published_at"])
                    ).total_seconds()
                    / 3600.0,
                    0.0,
                ),
                str(row["id"]),
            ),
        )
        published_at = _utc_datetime(worst_row["published_at"])
        fetched_at = _utc_datetime(worst_row["fetched_at"])
        worst = WorstPublishLatency(
            advisory_id=worst_row["id"],
            feed=worst_row["feed"],
            latency_hours=max(
                (fetched_at - published_at).total_seconds() / 3600.0,
                0.0,
            ),
        )

    return PublishLatencyResponse(
        days=days,
        feed=feed,
        series=series,
        worst=worst,
    )


@router.get(
    "/vuln/advisories/publish-latency",
    response_model=PublishLatencyResponse,
)
def publish_latency(
    days: int = Query(default=30),
    feed: Optional[str] = Query(default=None),
    db: Session = Depends(get_session),
) -> PublishLatencyResponse:
    bounded_days = max(1, min(365, days))
    dialect_name = db.get_bind().dialect.name

    if dialect_name == "postgresql":
        return _query_postgres(db, bounded_days, feed)
    return _query_sqlite(db, bounded_days, feed)


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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    feed VARCHAR,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    package VARCHAR,
                    published_at DATETIME,
                    fetched_at DATETIME
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    today = now.replace(hour=8, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    seed_rows = [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "feed": "osv",
            "severity": "high",
            "ecosystem": "PyPI",
            "package": "two-hour-package",
            "published_at": yesterday,
            "fetched_at": yesterday + timedelta(hours=2),
        },
        {
            "id": "00000000-0000-0000-0000-000000000002",
            "feed": "osv",
            "severity": "critical",
            "ecosystem": "npm",
            "package": "ten-hour-package",
            "published_at": today,
            "fetched_at": today + timedelta(hours=10),
        },
        {
            "id": "00000000-0000-0000-0000-000000000003",
            "feed": "github",
            "severity": "medium",
            "ecosystem": "PyPI",
            "package": "four-hour-package",
            "published_at": today + timedelta(hours=1),
            "fetched_at": today + timedelta(hours=5),
        },
    ]

    with SessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id,
                    feed,
                    severity,
                    ecosystem,
                    package,
                    published_at,
                    fetched_at
                ) VALUES (
                    :id,
                    :feed,
                    :severity,
                    :ecosystem,
                    :package,
                    :published_at,
                    :fetched_at
                )
                """
            ),
            [
                {
                    **row,
                    "published_at": row["published_at"].strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "fetched_at": row["fetched_at"].strftime("%Y-%m-%d %H:%M:%S"),
                }
                for row in seed_rows
            ],
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/vuln/advisories/publish-latency",
            params={"days": 30, "feed": "osv"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 30
        assert payload["feed"] == "osv"
        assert len(payload["series"]) == 2

        single_advisory_day = next(
            point
            for point in payload["series"]
            if point["advisory_count"] == 1
        )
        assert abs(single_advisory_day["mean_latency_hours"] - 2.0) <= 0.01
        assert payload["worst"] is not None
        assert payload["worst"]["advisory_id"] == seed_rows[1]["id"]
        assert abs(payload["worst"]["latency_hours"] - 10.0) <= 0.01

    print("PASS")


if __name__ == "__main__":
    _run_self_test()