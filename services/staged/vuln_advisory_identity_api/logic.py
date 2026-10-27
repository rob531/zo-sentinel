from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import VulnAdvisory


class AdvisoryIdentity(BaseModel):
    identity: str
    advisory_id: str
    feed: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    severity: Optional[str] = None
    source_url: Optional[str] = None


class AdvisoryIdentitiesResponse(BaseModel):
    generated_at: str
    total: int
    identities: list[AdvisoryIdentity]


router = APIRouter(prefix="/api")


def _identity_values(raw_identities: Any) -> list[str]:
    values = raw_identities
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except (TypeError, ValueError):
            return []

    if not isinstance(values, list):
        return []

    identities: list[str] = []
    for value in values:
        if isinstance(value, str):
            identity = value.strip()
            if identity:
                identities.append(identity)
        elif isinstance(value, dict):
            identity = value.get("id")
            if isinstance(identity, str):
                identity = identity.strip()
                if identity:
                    identities.append(identity)
    return identities


def extract_identities(rows: list[dict]) -> list[dict]:
    extracted: list[dict] = []
    for row in rows:
        advisory_id = row.get("advisory_id", row.get("id"))
        if advisory_id is None:
            continue

        for identity in _identity_values(row.get("identities")):
            extracted.append(
                {
                    "identity": identity,
                    "advisory_id": str(advisory_id),
                    "feed": row.get("feed"),
                    "ecosystem": row.get("ecosystem"),
                    "package": row.get("package"),
                    "severity": row.get("severity"),
                    "source_url": row.get("source_url"),
                }
            )
    return extracted


def get_vuln_advisory_identities(
    session,
    ecosystem: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    query = session.query(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnAdvisory.severity,
        VulnAdvisory.identities,
        VulnAdvisory.source_url,
    ).order_by(VulnAdvisory.id)

    if ecosystem is not None:
        query = query.filter(VulnAdvisory.ecosystem == ecosystem)

    rows = [
        {
            "id": row.id,
            "feed": row.feed,
            "ecosystem": row.ecosystem,
            "package": row.package,
            "severity": row.severity,
            "identities": row.identities,
            "source_url": row.source_url,
        }
        for row in query.all()
    ]

    all_identities = extract_identities(rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(all_identities),
        "identities": all_identities[offset : offset + limit],
    }


@router.get(
    "/vuln/advisories/identities",
    response_model=AdvisoryIdentitiesResponse,
)
def list_vuln_advisory_identities(
    ecosystem: Optional[str] = None,
    limit: int = Query(default=100),
    offset: int = Query(default=0),
    session=Depends(get_session),
) -> dict:
    return get_vuln_advisory_identities(
        session=session,
        ecosystem=ecosystem,
        limit=limit,
        offset=offset,
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
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
    TestingSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestingSession() as test_session:
        test_session.add_all(
            [
                VulnAdvisory(
                    id="advisory-1",
                    feed="osv",
                    ecosystem="PyPI",
                    package="example-one",
                    severity="HIGH",
                    summary="First advisory",
                    identities=["CVE-2024-0001"],
                    aliases=[],
                    source_url="https://example.test/advisory-1",
                    published_at=now,
                    fetched_at=now,
                    affected_ranges=[],
                    content_hash="advisory-hash-1",
                ),
                VulnAdvisory(
                    id="advisory-2",
                    feed="github",
                    ecosystem="npm",
                    package="example-two",
                    severity="MEDIUM",
                    summary="Second advisory",
                    identities=[{"id": "GHSA-xxxx"}],
                    aliases=[],
                    source_url="https://example.test/advisory-2",
                    published_at=now,
                    fetched_at=now,
                    affected_ranges=[],
                    content_hash="advisory-hash-2",
                ),
                VulnAdvisory(
                    id="advisory-3",
                    feed="vendor",
                    ecosystem="PyPI",
                    package="example-three",
                    severity="LOW",
                    summary="Third advisory",
                    identities=None,
                    aliases=[],
                    source_url="https://example.test/advisory-3",
                    published_at=now,
                    fetched_at=now,
                    affected_ranges=[],
                    content_hash="advisory-hash-3",
                ),
            ]
        )
        test_session.commit()

    def override_get_session():
        test_session = TestingSession()
        try:
            yield test_session
        finally:
            test_session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/vuln/advisories/identities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert len(payload["identities"]) == 2
    assert "GHSA-xxxx" in {
        item["identity"] for item in payload["identities"]
    }
    assert all(
        item["advisory_id"] != "advisory-3"
        for item in payload["identities"]
    )

    print("PASS")