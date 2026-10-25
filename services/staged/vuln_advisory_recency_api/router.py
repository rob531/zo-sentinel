from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import compute_recency

router = APIRouter(prefix="/api", tags=["vuln-advisories"])


class RecencyBucket(BaseModel):
    window_days: int
    window_label: str
    count: int


class SeverityCount(BaseModel):
    severity: str
    count: int


class RecencyResponse(BaseModel):
    days: int
    generated_at: str
    buckets: list[RecencyBucket]
    total: int
    by_severity: list[SeverityCount]


@router.get(
    "/vuln/advisories/recency",
    response_model=RecencyResponse,
    summary="Get vulnerability advisory recency buckets",
)
def get_vuln_advisory_recency(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session),
) -> RecencyResponse:
    statement = select(
        VulnAdvisory.published_at,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.feed,
        VulnAdvisory.fetched_at,
    )
    rows = [dict(row) for row in session.execute(statement).mappings().all()]

    result = compute_recency(rows, days)
    result["days"] = days

    generated_at = result.get("generated_at")
    if isinstance(generated_at, datetime):
        result["generated_at"] = generated_at.isoformat()
    elif not isinstance(generated_at, str):
        result["generated_at"] = datetime.now(timezone.utc).isoformat()

    return RecencyResponse(**result)


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    published_at DATETIME,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    feed VARCHAR,
                    fetched_at DATETIME
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        advisories = [
            {
                "id": 1,
                "published_at": now - timedelta(days=2),
                "severity": "high",
                "ecosystem": "npm",
                "feed": "mcp",
                "fetched_at": now,
            },
            {
                "id": 2,
                "published_at": now - timedelta(days=40),
                "severity": "medium",
                "ecosystem": "pypi",
                "feed": "mcp",
                "fetched_at": now,
            },
            {
                "id": 3,
                "published_at": now - timedelta(days=120),
                "severity": "low",
                "ecosystem": "go",
                "feed": "mcp",
                "fetched_at": now,
            },
            {
                "id": 4,
                "published_at": None,
                "severity": "critical",
                "ecosystem": "cargo",
                "feed": "mcp",
                "fetched_at": now,
            },
        ]

        for advisory in advisories:
            session.execute(
                text(
                    """
                    INSERT INTO vuln_advisories
                        (id, published_at, severity, ecosystem, feed, fetched_at)
                    VALUES
                        (:id, :published_at, :severity, :ecosystem, :feed, :fetched_at)
                    """
                ),
                advisory,
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        default_response = client.get("/api/vuln/advisories/recency")
        assert default_response.status_code == 200
        default_payload = default_response.json()
        assert sum(bucket["count"] for bucket in default_payload["buckets"]) == 1
        assert default_payload["total"] == 1

        all_response = client.get(
            "/api/vuln/advisories/recency",
            params={"days": 365},
        )
        assert all_response.status_code == 200
        all_payload = all_response.json()
        non_null_published = sum(
            advisory["published_at"] is not None for advisory in advisories
        )
        assert sum(bucket["count"] for bucket in all_payload["buckets"]) == non_null_published
        assert all_payload["total"] == non_null_published

        seven_day_response = client.get(
            "/api/vuln/advisories/recency",
            params={"days": 7},
        )
        assert seven_day_response.status_code == 200
        seven_day_payload = seven_day_response.json()
        assert sum(bucket["count"] for bucket in seven_day_payload["buckets"]) == 1
        assert seven_day_payload["total"] == 1

    print("PASS")