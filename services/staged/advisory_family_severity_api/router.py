from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import FamilySeverityResponse, get_family_severity

router = APIRouter(prefix="/api/vuln/family-severity", tags=["vulnerability"])


@router.get("", response_model=FamilySeverityResponse)
def read_family_severity(
    days: Annotated[int, Query(description="Published-advisory lookback in days")] = 90,
    severity: Annotated[
        str | None, Query(description="Exact advisory severity filter")
    ] = None,
    ecosystem: Annotated[
        str | None, Query(description="Exact package ecosystem filter")
    ] = None,
    limit: Annotated[int, Query(description="Maximum number of family rows")] = 50,
    session: Session = Depends(get_session),
) -> FamilySeverityResponse:
    return get_family_severity(
        session=session,
        days=max(1, min(days, 365)),
        severity=severity,
        ecosystem=ecosystem,
        limit=max(1, min(limit, 200)),
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import VulnAdvisory, VulnLink

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine, checkfirst=True)
    VulnLink.__table__.create(engine, checkfirst=True)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with testing_session() as session:
        session.execute(
            VulnAdvisory.__table__.insert(),
            [
                {
                    "id": 1,
                    "feed": "test-feed",
                    "summary": "Shared package advisory one",
                    "severity": "HIGH",
                    "ecosystem": "npm",
                    "package": "shared-package",
                    "affected_ranges": [],
                    "aliases": [],
                    "source_url": "https://example.invalid/advisory/1",
                    "published_at": now,
                    "fetched_at": now,
                    "identities": {},
                    "content_hash": "hash-1",
                },
                {
                    "id": 2,
                    "feed": "test-feed",
                    "summary": "Shared package advisory two",
                    "severity": "HIGH",
                    "ecosystem": "npm",
                    "package": "shared-package",
                    "affected_ranges": [],
                    "aliases": [],
                    "source_url": "https://example.invalid/advisory/2",
                    "published_at": now,
                    "fetched_at": now,
                    "identities": {},
                    "content_hash": "hash-2",
                },
                {
                    "id": 3,
                    "feed": "test-feed",
                    "summary": "Other package advisory",
                    "severity": "MEDIUM",
                    "ecosystem": "pypi",
                    "package": "other-package",
                    "affected_ranges": [],
                    "aliases": [],
                    "source_url": "https://example.invalid/advisory/3",
                    "published_at": now,
                    "fetched_at": now,
                    "identities": {},
                    "content_hash": "hash-3",
                },
            ],
        )
        session.execute(
            VulnLink.__table__.insert(),
            [
                {
                    "id": 1,
                    "advisory_id": 1,
                    "server_id": "server-one",
                    "match_basis": "package",
                    "match_value": "shared-package",
                    "match_confidence": 0.80,
                    "linked_at": now,
                },
                {
                    "id": 2,
                    "advisory_id": 2,
                    "server_id": "server-two",
                    "match_basis": "package",
                    "match_value": "shared-package",
                    "match_confidence": 0.90,
                    "linked_at": now,
                },
                {
                    "id": 3,
                    "advisory_id": 3,
                    "server_id": "server-one",
                    "match_basis": "package",
                    "match_value": "other-package",
                    "match_confidence": 0.70,
                    "linked_at": now,
                },
            ],
        )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/vuln/family-severity",
            params={"days": 365, "limit": 50},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["families"]) == 2, payload

        shared = next(
            row
            for row in payload["families"]
            if row["ecosystem"] == "npm" and row["package"] == "shared-package"
        )
        assert shared["advisory_count"] == 2, shared
        assert shared["linked_server_count"] == 2, shared
        assert 0 <= shared["avg_match_confidence"] <= 1, shared

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()