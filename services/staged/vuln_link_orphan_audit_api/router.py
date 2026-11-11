from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from .logic import get_orphan_audit

router = APIRouter(prefix="/api", tags=["vuln-links"])


class OrphanExample(BaseModel):
    link_id: int | None
    advisory_id: str | None
    server_id: str | None


class ConfidenceBuckets(BaseModel):
    low: int
    mid: int
    high: int


class OrphanAuditResponse(BaseModel):
    total_links: int
    total_advisories: int
    links_without_advisory: int
    links_without_server: int
    advisories_without_links: int
    confidence_buckets: ConfidenceBuckets
    orphan_examples: list[OrphanExample]


@router.get("/vuln-links/orphan-audit", response_model=OrphanAuditResponse)
def orphan_audit(session: Session = Depends(get_session)) -> OrphanAuditResponse:
    return OrphanAuditResponse.model_validate(get_orphan_audit(session))


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)

    with TestSession() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id="adv-valid",
                    feed="test",
                    summary="Valid advisory",
                    severity="medium",
                    ecosystem="python",
                    package="example-package",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=["ADV-1"],
                    source_url="https://example.test/advisory",
                    published_at=datetime.now(timezone.utc),
                    fetched_at=datetime.now(timezone.utc),
                    identities={"cve": ["CVE-0000-0001"]},
                    content_hash="advisory-hash",
                ),
                VulnAdvisory(
                    id="adv-unlinked",
                    feed="test",
                    summary="Unlinked advisory",
                    severity="low",
                    ecosystem="python",
                    package="other-package",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=[],
                    source_url="https://example.test/unlinked",
                    published_at=datetime.now(timezone.utc),
                    fetched_at=datetime.now(timezone.utc),
                    identities={},
                    content_hash="unlinked-hash",
                ),
                McpServerRegistry(
                    server_id="srv-valid",
                    name="Valid server",
                    risk_tier="medium",
                ),
                VulnLink(
                    id=1,
                    advisory_id="missing-adv",
                    server_id="srv-valid",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.2,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    id=2,
                    advisory_id="adv-valid",
                    server_id="missing-srv",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.5,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    id=3,
                    advisory_id="adv-valid",
                    server_id="srv-valid",
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.9,
                    linked_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    response = TestClient(test_app).get("/api/vuln-links/orphan-audit")
    response.raise_for_status()
    payload = response.json()

    assert payload["total_links"] == 3
    assert payload["total_advisories"] == 2
    assert payload["links_without_advisory"] == 1
    assert payload["links_without_server"] == 1
    assert payload["advisories_without_links"] == 1
    assert sum(payload["confidence_buckets"].values()) == 3
    assert len(payload["orphan_examples"]) <= 20

    print("PASS")