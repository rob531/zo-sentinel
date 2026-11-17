"""HTTP surface for the vulnerability advisory severity spread service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

from .logic import advisory_severity_spread

router = APIRouter(prefix="/api", tags=["vuln"])

SEVERITY_BUCKETS = ("critical", "high", "medium", "low", "unknown")


class EcosystemSeveritySpread(BaseModel):
    ecosystem: str
    total: int
    critical: int
    high: int


class AdvisorySeveritySpreadResponse(BaseModel):
    days: int
    ecosystem: str | None
    total: int
    by_severity: dict[str, int]
    by_ecosystem: list[EcosystemSeveritySpread]


@router.get(
    "/vuln/advisory-severity-spread",
    response_model=AdvisorySeveritySpreadResponse,
)
def get_advisory_severity_spread(
    days: int = Query(30, ge=1),
    ecosystem: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> AdvisorySeveritySpreadResponse:
    spread = advisory_severity_spread(db, days=days, ecosystem=ecosystem)
    raw_severity = spread.get("by_severity", {})
    raw_ecosystems = spread.get("by_ecosystem", [])

    by_severity = {
        severity: int(raw_severity.get(severity, 0))
        for severity in SEVERITY_BUCKETS
    }
    by_ecosystem = [
        EcosystemSeveritySpread(
            ecosystem=str(item["ecosystem"]),
            total=int(item["total"]),
            critical=int(item.get("critical", 0)),
            high=int(item.get("high", 0)),
        )
        for item in raw_ecosystems
    ]

    return AdvisorySeveritySpreadResponse(
        days=days,
        ecosystem=ecosystem,
        total=int(spread.get("total", sum(by_severity.values()))),
        by_severity=by_severity,
        by_ecosystem=by_ecosystem,
    )


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as test_db:
        now = datetime.now(timezone.utc)
        test_db.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="test-feed",
                    severity="critical",
                    ecosystem="npm",
                    package="example-critical",
                    affected_ranges=">=1.0.0",
                    aliases="[]",
                    source_url="https://example.test/critical",
                    published_at=now,
                    fetched_at=now,
                    content_hash="test-content-hash-1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="test-feed",
                    severity="high",
                    ecosystem="npm",
                    package="example-high",
                    affected_ranges=">=2.0.0",
                    aliases="[]",
                    source_url="https://example.test/high",
                    published_at=now,
                    fetched_at=now,
                    content_hash="test-content-hash-2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="test-feed",
                    severity="high",
                    ecosystem="pypi",
                    package="example-pypi",
                    affected_ranges=">=3.0.0",
                    aliases="[]",
                    source_url="https://example.test/pypi",
                    published_at=now - timedelta(days=1),
                    fetched_at=now,
                    content_hash="test-content-hash-3",
                ),
            ]
        )
        test_db.commit()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = lambda: test_db
        client = TestClient(test_app)

        response = client.get(
            "/api/vuln/advisory-severity-spread",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["by_severity"]["critical"] == 1
        assert payload["by_severity"]["high"] == 2

        filtered = client.get(
            "/api/vuln/advisory-severity-spread",
            params={"days": 30, "ecosystem": "npm"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total"] == 2
        assert filtered_payload["ecosystem"] == "npm"
        assert filtered_payload["by_severity"]["critical"] == 1
        assert filtered_payload["by_severity"]["high"] == 1

    print("PASS")