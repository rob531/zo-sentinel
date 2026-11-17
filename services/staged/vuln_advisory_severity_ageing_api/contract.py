from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import get_session
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api", tags=["vulnerability advisory ageing"])

BAND_LABELS = ("0-7", "8-30", "31-90", "91-180", "180+")


class AgeingBand(BaseModel):
    band: str = Field(description="Age band in days")
    severity: str = Field(description="Advisory severity")
    count: int = Field(ge=0, description="Number of advisories in the band")


class AdvisoryAgeingResponse(BaseModel):
    days: int = Field(ge=0, description="Number of days after which an advisory is stale")
    bands: list[AgeingBand]
    stale_counts: dict[str, int]
    total: int = Field(ge=0)


def _as_utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.combine(value, datetime.min.time())

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_in_days(published_at: Any, now: datetime) -> int:
    published = _as_utc_datetime(published_at)
    seconds = (now - published).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 86400)


def _band_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    return "180+"


def get_advisory_ageing(
    days: int = Query(default=30, ge=0, le=36500),
    session: Session = Depends(get_session),
) -> AdvisoryAgeingResponse:
    """Return vulnerability advisory counts by severity and publication-age band.

    The query deliberately uses only portable SQL. The cutoff is bound as a
    parameter rather than interpolated into the statement.
    """

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows = session.execute(
        text(
            """
            SELECT
                severity,
                published_at,
                CASE WHEN published_at < :cutoff THEN 1 ELSE 0 END AS is_stale
            FROM vuln_advisories
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    band_counts: Counter[tuple[str, str]] = Counter()
    stale_counts: Counter[str] = Counter()
    severities: set[str] = set()
    total = 0

    for row in rows:
        severity = str(row["severity"] or "unknown")
        severities.add(severity)
        total += 1

        age_days = _age_in_days(row["published_at"], now)
        band_counts[(_band_for_age(age_days), severity)] += 1

        if int(row["is_stale"] or 0) == 1:
            stale_counts[severity] += 1

    bands = [
        AgeingBand(band=band, severity=severity, count=count)
        for band in BAND_LABELS
        for severity in sorted(severities)
        if (count := band_counts[(band, severity)]) > 0
    ]

    return AdvisoryAgeingResponse(
        days=days,
        bands=bands,
        stale_counts={severity: stale_counts[severity] for severity in sorted(severities)},
        total=total,
    )


@router.get(
    "/vuln/advisories/ageing",
    response_model=AdvisoryAgeingResponse,
    summary="Summarise vulnerability advisories by age and severity",
)
def advisory_ageing(
    days: int = Query(default=30, ge=0, le=36500),
    session: Session = Depends(get_session),
) -> AdvisoryAgeingResponse:
    return get_advisory_ageing(days=days, session=session)


def _run_self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    published_at DATETIME,
                    fetched_at DATETIME,
                    source_url TEXT
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(microsecond=0)
        advisories = [
            (
                1,
                "test-feed",
                "critical",
                "pypi",
                "alpha",
                now - timedelta(days=1),
                now,
                "https://example.invalid/advisories/1",
            ),
            (
                2,
                "test-feed",
                "high",
                "npm",
                "bravo",
                now - timedelta(days=5),
                now,
                "https://example.invalid/advisories/2",
            ),
            (
                3,
                "test-feed",
                "medium",
                "go",
                "charlie",
                now - timedelta(days=45),
                now,
                "https://example.invalid/advisories/3",
            ),
            (
                4,
                "test-feed",
                "low",
                "cargo",
                "delta",
                now - timedelta(days=200),
                now,
                "https://example.invalid/advisories/4",
            ),
        ]
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, severity, ecosystem, package,
                    published_at, fetched_at, source_url
                ) VALUES (
                    :id, :feed, :severity, :ecosystem, :package,
                    :published_at, :fetched_at, :source_url
                )
                """
            ),
            [
                {
                    "id": row[0],
                    "feed": row[1],
                    "severity": row[2],
                    "ecosystem": row[3],
                    "package": row[4],
                    "published_at": row[5],
                    "fetched_at": row[6],
                    "source_url": row[7],
                }
                for row in advisories
            ],
        )

    def override_get_session():
        with testing_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/advisories/ageing", params={"days": 365})
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total"] == 4, payload
            assert payload["days"] == 365, payload

            expected_zero_to_seven = sum(
                1
                for item in payload["bands"]
                if item["band"] == "0-7"
            )
            assert expected_zero_to_seven == 2, payload

            counted_total = sum(item["count"] for item in payload["bands"])
            assert counted_total == payload["total"], payload
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()