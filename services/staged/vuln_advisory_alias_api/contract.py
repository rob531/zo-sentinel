import datetime
import json
import sys
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api")


@router.get(
    "/vuln/advisories/{advisory_id}/aliases",
    response_model=dict,
    tags=["vuln_advisory_alias"],
)
def get_advisory_aliases(
    advisory_id: int, db: Session = Depends(get_session)
) -> dict:
    advisory = db.get(VulnAdvisory, advisory_id)
    if advisory is None:
        raise HTTPException(status_code=404, detail=f"Advisory {advisory_id} not found")

    links = db.execute(
        select(VulnLink).where(VulnLink.advisory_id == advisory_id)
    ).scalars().all()

    matched_servers = [
        {
            "server_id": link.server_id,
            "match_basis": link.match_basis,
            "match_value": link.match_value,
            "match_confidence": link.match_confidence,
        }
        for link in links
    ]

    return {
        "advisory_id": advisory.id,
        "feed": advisory.feed,
        "severity": advisory.severity,
        "ecosystem": advisory.ecosystem,
        "package": advisory.package,
        "summary": advisory.summary,
        "source_url": advisory.source_url,
        "published_at": advisory.published_at.isoformat()
        if isinstance(advisory.published_at, datetime.datetime)
        else advisory.published_at,
        "aliases": advisory.aliases or [],
        "identities": advisory.identities or [],
        "affected_ranges": advisory.affected_ranges or [],
        "matched_servers": matched_servers,
        "matched_server_count": len(matched_servers),
    }


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.vuln_advisory_alias_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # --------------------------------------------------------------------- #
    # In‑memory SQLite setup (StaticPool) – overrides the real DB dependency
    # --------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create tables for the two models
    VulnAdvisory.__table__.metadata.create_all(engine)

    # Dependency override
    def get_test_session() -> Session:
        with SessionLocal() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # --------------------------------------------------------------------- #
    # Seed test data
    # --------------------------------------------------------------------- #
    with SessionLocal() as db:
        advisory = VulnAdvisory(
            id=1,
            feed="test_feed",
            severity="high",
            ecosystem="npm",
            package="example-pkg",
            summary="Test advisory summary",
            source_url="https://example.com/advisory/1",
            published_at=datetime.datetime.utcnow(),
            aliases=["CVE-2023-0001", "GHSA-xxxx-xxxx-xxxx"],
            identities=["id-1", "id-2"],
            affected_ranges=[{"type": "SEMVER", "introduced": "1.0.0", "fixed": "2.0.0"}],
        )
        db.add(advisory)

        link1 = VulnLink(
            advisory_id=1,
            server_id="srv-01",
            match_basis="package",
            match_value="example-pkg",
            match_confidence=0.95,
            linked_at=datetime.datetime.utcnow(),
        )
        link2 = VulnLink(
            advisory_id=1,
            server_id="srv-02",
            match_basis="ecosystem",
            match_value="npm",
            match_confidence=0.90,
            linked_at=datetime.datetime.utcnow(),
        )
        db.add_all([link1, link2])
        db.commit()

    # --------------------------------------------------------------------- #
    # Run acceptance tests
    # --------------------------------------------------------------------- #
    client = TestClient(app)

    # Successful fetch
    resp = client.get("/api/vuln/advisories/1/aliases")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, dict), "Response is not a dict"
    assert len(data.get("aliases", [])) == 2, "Aliases count mismatch"
    assert data.get("matched_server_count") == 2, "Matched server count mismatch"

    # Missing advisory
    resp_missing = client.get("/api/vuln/advisories/999/aliases")
    assert resp_missing.status_code == 404, "Missing advisory did not return 404"

    print("PASS")
    sys.exit(0)