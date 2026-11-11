from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/vuln/advisories", tags=["vuln-advisories"])


class AdvisoryAgeingBand(BaseModel):
    band: str
    severity: str
    count: int


class AdvisoryAgeingResponse(BaseModel):
    days: int
    bands: list[AdvisoryAgeingBand]
    stale_counts: dict[str, int]
    total: int


def _severity_counts(
    session: Session, *conditions: object
) -> list[tuple[str, int]]:
    statement = select(
        VulnAdvisory.severity,
        func.count(VulnAdvisory.id),
    ).group_by(VulnAdvisory.severity)

    for condition in conditions:
        statement = statement.where(condition)

    rows = session.execute(statement).all()
    return [
        (severity if severity is not None else "unknown", int(count))
        for severity, count in rows
    ]


@router.get("/ageing", response_model=AdvisoryAgeingResponse)
def get_advisory_severity_ageing(
    days: int = Query(default=30, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> AdvisoryAgeingResponse:
    now = datetime.now(timezone.utc)
    day = timedelta(days=1)

    cutoff_7 = now - (8 * day)
    cutoff_30 = now - (31 * day)
    cutoff_90 = now - (91 * day)
    cutoff_180 = now - (181 * day)
    stale_cutoff = now - (days * day)

    band_ranges = (
        ("0-7", None, cutoff_7),
        ("8-30", cutoff_7, cutoff_30),
        ("31-90", cutoff_30, cutoff_90),
        ("91-180", cutoff_90, cutoff_180),
        ("180+", cutoff_180, None),
    )

    bands: list[AdvisoryAgeingBand] = []
    for band, lower, upper in band_ranges:
        conditions = []
        if lower is not None:
            conditions.append(VulnAdvisory.published_at >= lower)
        if upper is not None:
            conditions.append(VulnAdvisory.published_at < upper)

        for severity, count in sorted(
            _severity_counts(session, *conditions),
            key=lambda item: item[0].casefold(),
        ):
            bands.append(
                AdvisoryAgeingBand(
                    band=band,
                    severity=severity,
                    count=count,
                )
            )

    stale_counts = {
        severity: count
        for severity, count in sorted(
            _severity_counts(session, VulnAdvisory.published_at < stale_cutoff),
            key=lambda item: item[0].casefold(),
        )
    }

    total = int(
        session.execute(
            select(func.count()).select_from(VulnAdvisory)
        ).scalar_one()
    )

    return AdvisoryAgeingResponse(
        days=days,
        bands=bands,
        stale_counts=stale_counts,
        total=total,
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    ecosystem TEXT NOT NULL,
                    package TEXT NOT NULL,
                    published_at DATETIME NOT NULL,
                    fetched_at DATETIME NOT NULL,
                    source_url TEXT NOT NULL
                )
                """
            )
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        {
            "id": 1,
            "feed": "test-feed",
            "severity": "critical",
            "ecosystem": "pypi",
            "package": "example-critical",
            "published_at": (now - timedelta(days=1)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            ),
            "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "source_url": "https://example.test/critical",
        },
        {
            "id": 2,
            "feed": "test-feed",
            "severity": "high",
            "ecosystem": "npm",
            "package": "example-high",
            "published_at": (now - timedelta(days=5)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            ),
            "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "source_url": "https://example.test/high",
        },
        {
            "id": 3,
            "feed": "test-feed",
            "severity": "medium",
            "ecosystem": "go",
            "package": "example-medium",
            "published_at": (now - timedelta(days=45)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            ),
            "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "source_url": "https://example.test/medium",
        },
        {
            "id": 4,
            "feed": "test-feed",
            "severity": "low",
            "ecosystem": "cargo",
            "package": "example-low",
            "published_at": (now - timedelta(days=200)).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            ),
            "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "source_url": "https://example.test/low",
        },
    ]

    with engine.begin() as connection:
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
            rows,
        )

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Generator[Session, None, None]:
        database_session = testing_session()
        try:
            yield database_session
        finally:
            database_session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/advisories/ageing", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4, payload
        assert (
            sum(
                item["count"]
                for item in payload["bands"]
                if item["band"] == "0-7"
            )
            == 2
        ), payload
        assert payload["stale_counts"].get("medium") == 1, payload
        assert payload["stale_counts"].get("low") == 1, payload

    print("PASS")


if __name__ == "__main__":
    _self_test()