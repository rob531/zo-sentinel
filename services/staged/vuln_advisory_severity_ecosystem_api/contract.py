from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


router = APIRouter(prefix="/api/vuln", tags=["vulnerability-advisories"])


class SeverityEcosystemBucket(BaseModel):
    severity: str
    ecosystem: str
    count: int


class SeverityEcosystemResponse(BaseModel):
    days: int
    ecosystem: Optional[str]
    buckets: list[SeverityEcosystemBucket]


@router.get(
    "/advisory-severity-ecosystem",
    response_model=SeverityEcosystemResponse,
)
def advisory_severity_ecosystem(
    ecosystem: Optional[str] = Query(default=None),
    days: int = Query(default=90),
    session: Session = Depends(get_session),
) -> SeverityEcosystemResponse:
    effective_days = max(1, min(730, days))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=effective_days
    )

    severity = func.coalesce(VulnAdvisory.severity, "unknown")
    advisory_ecosystem = func.coalesce(VulnAdvisory.ecosystem, "unknown")

    statement = (
        select(
            severity.label("severity"),
            advisory_ecosystem.label("ecosystem"),
            func.count().label("count"),
        )
        .where(VulnAdvisory.published_at >= cutoff)
        .group_by(severity, advisory_ecosystem)
        .order_by(func.count().desc(), severity.asc())
    )
    if ecosystem is not None:
        statement = statement.where(VulnAdvisory.ecosystem == ecosystem)

    rows = session.execute(statement).all()
    buckets = [
        SeverityEcosystemBucket(
            severity=row.severity,
            ecosystem=row.ecosystem,
            count=int(row.count),
        )
        for row in rows
    ]
    return SeverityEcosystemResponse(
        days=effective_days,
        ecosystem=ecosystem,
        buckets=buckets,
    )


def _run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.engine import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    published_at DATETIME
                )
                """
            )
        )
        published_at = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (severity, ecosystem, published_at)
                VALUES
                    (:severity, :ecosystem, :published_at)
                """
            ),
            [
                {
                    "severity": "high",
                    "ecosystem": "pypi",
                    "published_at": published_at,
                },
                {
                    "severity": "high",
                    "ecosystem": "pypi",
                    "published_at": published_at,
                },
                {
                    "severity": "critical",
                    "ecosystem": "npm",
                    "published_at": published_at,
                },
                {
                    "severity": None,
                    "ecosystem": "npm",
                    "published_at": published_at,
                },
            ],
        )

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with local_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/advisory-severity-ecosystem")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["days"] == 90
        assert payload["ecosystem"] is None
        assert isinstance(payload["buckets"], list)

        buckets = {
            (bucket["severity"], bucket["ecosystem"]): bucket["count"]
            for bucket in payload["buckets"]
        }
        assert buckets[("high", "pypi")] == 2
        assert any(
            bucket["severity"] == "unknown" and bucket["count"] == 1
            for bucket in payload["buckets"]
        )
        assert sum(bucket["count"] for bucket in payload["buckets"]) == 4

        filtered = client.get(
            "/api/vuln/advisory-severity-ecosystem",
            params={"ecosystem": "pypi", "days": 30},
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["days"] == 30
        assert filtered_payload["ecosystem"] == "pypi"
        assert filtered_payload["buckets"] == [
            {"severity": "high", "ecosystem": "pypi", "count": 2}
        ]

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()