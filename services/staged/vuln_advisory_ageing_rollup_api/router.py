from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

try:
    from .logic import get_ageing_rollup as _logic_get_ageing_rollup
except ImportError:
    _logic_get_ageing_rollup = object()


router = APIRouter(prefix="/api", tags=["vuln-advisories"])


class AgeBuckets(BaseModel):
    zero_to_thirty: int = 0
    thirty_one_to_ninety: int = 0
    ninety_one_to_three_hundred_sixty_five: int = 0
    over_three_hundred_sixty_five: int = 0


class SeverityAgeing(BaseModel):
    severity: str
    total: int
    oldest_published_at: str | None
    newest_published_at: str | None
    age_buckets: dict[str, int]


class EcosystemTotal(BaseModel):
    ecosystem: str
    total: int


class AdvisoryAgeingResponse(BaseModel):
    total: int
    by_severity: list[SeverityAgeing]
    ecosystems: list[EcosystemTotal]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso8601(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _age_bucket(published_at: datetime | None, now: datetime) -> str:
    if published_at is None:
        return "365d+"

    normalized = _as_utc(published_at)
    assert normalized is not None
    age_days = max(0, int((now - normalized).total_seconds() // 86400))

    if age_days <= 30:
        return "0-30d"
    if age_days <= 90:
        return "31-90d"
    if age_days <= 365:
        return "91-365d"
    return "365d+"


def _build_ageing_rollup(session: Session) -> dict[str, Any]:
    advisories = session.query(VulnAdvisory).all()
    now = datetime.now(timezone.utc)

    severity_rows: dict[str, list[Any]] = defaultdict(list)
    ecosystem_counts: dict[str, int] = defaultdict(int)

    for advisory in advisories:
        severity = str(advisory.severity)
        ecosystem = str(advisory.ecosystem)
        severity_rows[severity].append(advisory)
        ecosystem_counts[ecosystem] += 1

    by_severity: list[dict[str, Any]] = []

    for severity, rows in severity_rows.items():
        buckets = {
            "0-30d": 0,
            "31-90d": 0,
            "91-365d": 0,
            "365d+": 0,
        }
        published_values: list[datetime] = []
        fetched_after_published_count = 0

        for advisory in rows:
            published_at = advisory.published_at
            fetched_at = advisory.fetched_at

            if published_at is not None:
                published_values.append(published_at)

            buckets[_age_bucket(published_at, now)] += 1

            normalized_published = _as_utc(published_at)
            normalized_fetched = _as_utc(fetched_at)
            if (
                normalized_published is not None
                and normalized_fetched is not None
                and normalized_fetched > normalized_published
            ):
                fetched_after_published_count += 1

        oldest = min(
            published_values,
            key=lambda value: _as_utc(value) or datetime.min.replace(tzinfo=timezone.utc),
        ) if published_values else None
        newest = max(
            published_values,
            key=lambda value: _as_utc(value) or datetime.min.replace(tzinfo=timezone.utc),
        ) if published_values else None

        by_severity.append(
            {
                "severity": severity,
                "total": len(rows),
                "oldest_published_at": _iso8601(oldest),
                "newest_published_at": _iso8601(newest),
                "age_buckets": buckets,
                "_fetched_after_published_count": fetched_after_published_count,
            }
        )

    by_severity.sort(key=lambda row: (-row["total"], row["severity"]))

    ecosystems = [
        {"ecosystem": ecosystem, "total": total}
        for ecosystem, total in ecosystem_counts.items()
    ]
    ecosystems.sort(key=lambda row: (-row["total"], row["ecosystem"]))

    return {
        "total": len(advisories),
        "by_severity": by_severity,
        "ecosystems": ecosystems,
    }


@router.get(
    "/vuln/advisories/ageing",
    response_model=AdvisoryAgeingResponse,
    summary="Summarise advisory age by severity",
)
def get_vuln_advisory_ageing(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    result = _build_ageing_rollup(session)
    for row in result["by_severity"]:
        row.pop("_fetched_after_published_count", None)
    return result


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine)
    TestSession = sessionmaker(bind=engine)

    reference = datetime.now(timezone.utc).replace(microsecond=0)
    seeded = [
        VulnAdvisory(
            id="ageing-1",
            feed="test",
            summary="five days old",
            severity="critical",
            ecosystem="pypi",
            package="example-one",
            published_at=reference - timedelta(days=5),
            fetched_at=reference - timedelta(days=4),
            source_url="https://example.invalid/1",
        ),
        VulnAdvisory(
            id="ageing-2",
            feed="test",
            summary="forty-five days old",
            severity="critical",
            ecosystem="npm",
            package="example-two",
            published_at=reference - timedelta(days=45),
            fetched_at=reference - timedelta(days=44),
            source_url="https://example.invalid/2",
        ),
        VulnAdvisory(
            id="ageing-3",
            feed="test",
            summary="two hundred days old",
            severity="high",
            ecosystem="pypi",
            package="example-three",
            published_at=reference - timedelta(days=200),
            fetched_at=reference - timedelta(days=199),
            source_url="https://example.invalid/3",
        ),
        VulnAdvisory(
            id="ageing-4",
            feed="test",
            summary="five hundred days old",
            severity="high",
            ecosystem="npm",
            package="example-four",
            published_at=reference - timedelta(days=500),
            fetched_at=reference - timedelta(days=499),
            source_url="https://example.invalid/4",
        ),
    ]

    with TestSession() as session:
        session.add_all(seeded)
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisories/ageing")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 4
        assert len(payload["by_severity"]) == 2
        assert sum(
            sum(row["age_buckets"].values()) for row in payload["by_severity"]
        ) == 4
        assert all(isinstance(row["oldest_published_at"], str) for row in payload["by_severity"])
        assert all(isinstance(row["newest_published_at"], str) for row in payload["by_severity"])

    print("PASS")