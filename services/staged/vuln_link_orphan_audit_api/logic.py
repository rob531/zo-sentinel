from typing import Any, Dict

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def _resolve_session(session: Any) -> Any:
    if hasattr(session, "dependency") and hasattr(session, "dependency_overrides"):
        dependency = session.dependency
        if callable(dependency):
            return next(dependency)
    return session


def _build_orphan_audit(session: Any) -> Dict[str, Any]:
    rows = session.execute(
        select(
            VulnLink.id.label("link_id"),
            VulnLink.advisory_id.label("link_advisory_id"),
            VulnLink.server_id.label("link_server_id"),
            VulnLink.match_confidence.label("match_confidence"),
            VulnAdvisory.id.label("matched_advisory_id"),
            McpServerRegistry.server_id.label("matched_server_id"),
        )
        .select_from(VulnLink)
        .outerjoin(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .outerjoin(
            McpServerRegistry,
            VulnLink.server_id == McpServerRegistry.server_id,
        )
    ).all()

    total_links = len(rows)
    links_without_advisory = 0
    links_without_server = 0
    confidence_buckets = {"low": 0, "mid": 0, "high": 0}
    orphan_examples = []

    for row in rows:
        if row.matched_advisory_id is None:
            links_without_advisory += 1
        if row.matched_server_id is None:
            links_without_server += 1

        confidence = row.match_confidence
        if confidence is None or confidence < 0.4:
            confidence_buckets["low"] += 1
        elif confidence < 0.75:
            confidence_buckets["mid"] += 1
        else:
            confidence_buckets["high"] += 1

        if (
            row.matched_advisory_id is None
            or row.matched_server_id is None
        ) and len(orphan_examples) < 20:
            orphan_examples.append(
                {
                    "link_id": row.link_id,
                    "advisory_id": row.link_advisory_id,
                    "server_id": row.link_server_id,
                }
            )

    total_advisories = session.execute(
        select(func.count(VulnAdvisory.id)).select_from(VulnAdvisory)
    ).scalar_one()

    advisories_without_links = session.execute(
        select(VulnAdvisory.id)
        .select_from(VulnAdvisory)
        .outerjoin(VulnLink, VulnLink.advisory_id == VulnAdvisory.id)
        .where(VulnLink.id.is_(None))
    ).all()

    return {
        "total_links": total_links,
        "total_advisories": total_advisories,
        "links_without_advisory": links_without_advisory,
        "links_without_server": links_without_server,
        "advisories_without_links": len(advisories_without_links),
        "confidence_buckets": confidence_buckets,
        "orphan_examples": orphan_examples,
    }


def get_orphan_audit(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_orphan_audit(_resolve_session(session))


def get_vuln_link_orphan_audit(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _build_orphan_audit(_resolve_session(session))


def audit_orphan_links(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _build_orphan_audit(_resolve_session(session))


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
    VulnAdvisory.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def seed(session):
        valid_advisory = VulnAdvisory(
            id="adv-valid",
            feed="test",
            summary="valid advisory",
            severity="medium",
            ecosystem="python",
            package="example-package",
            affected_ranges=[],
            aliases=[],
            source_url="https://example.invalid/advisory",
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            identities=[],
            content_hash="valid-hash",
        )
        unlinked_advisory = VulnAdvisory(
            id="adv-unlinked",
            feed="test",
            summary="unlinked advisory",
            severity="low",
            ecosystem="python",
            package="other-package",
            affected_ranges=[],
            aliases=[],
            source_url="https://example.invalid/unlinked",
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc),
            identities=[],
            content_hash="unlinked-hash",
        )
        server = McpServerRegistry(
            server_id="server-valid",
            name="Valid Server",
            risk_tier="standard",
        )
        session.add_all([valid_advisory, unlinked_advisory, server])
        session.flush()
        session.add_all(
            [
                VulnLink(
                    advisory_id="missing-adv",
                    server_id="server-valid",
                    match_basis="advisory",
                    match_value="missing-adv",
                    match_confidence=0.2,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    advisory_id="adv-valid",
                    server_id="missing-srv",
                    match_basis="server",
                    match_value="missing-srv",
                    match_confidence=0.5,
                    linked_at=datetime.now(timezone.utc),
                ),
                VulnLink(
                    advisory_id="adv-valid",
                    server_id="server-valid",
                    match_basis="both",
                    match_value="adv-valid",
                    match_confidence=0.9,
                    linked_at=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_session():
        with TestSession() as session:
            seed(session)
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/vuln-links/orphan-audit")
    def orphan_audit(session: Session = Depends(get_session)):
        return get_orphan_audit(session)

    with TestClient(test_app) as client:
        response = client.get("/api/vuln-links/orphan-audit")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_links"] == 3
        assert payload["total_advisories"] == 2
        assert payload["links_without_advisory"] == 1
        assert payload["links_without_server"] == 1
        assert payload["advisories_without_links"] == 1
        assert sum(payload["confidence_buckets"].values()) == 3
        assert len(payload["orphan_examples"]) <= 20

    print("PASS")