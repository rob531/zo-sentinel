"""Data and computation layer for advisory link severity mapping."""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_SEVERITIES = ("critical", "high", "medium", "low")


def get_advisory_severity_map(
    db: Session,
    server_id: Optional[str] = None,
    ecosystem: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Return linked advisories grouped into severity counts for each server."""
    statement = (
        select(VulnLink, VulnAdvisory)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
    )

    if server_id is not None:
        statement = statement.where(VulnLink.server_id == server_id)
    if ecosystem is not None:
        statement = statement.where(VulnAdvisory.ecosystem == ecosystem)
    if min_confidence is not None:
        statement = statement.where(VulnLink.match_confidence >= min_confidence)

    statement = statement.order_by(
        VulnLink.server_id,
        VulnAdvisory.severity,
        VulnAdvisory.published_at.desc(),
        VulnLink.id,
    )

    servers: Dict[str, Dict[str, Any]] = {}
    total_links = 0

    for link, advisory in db.execute(statement).all():
        server = servers.setdefault(
            str(link.server_id),
            {
                "server_id": str(link.server_id),
                "advisories": [],
                "counts_by_severity": {severity: 0 for severity in _SEVERITIES},
            },
        )

        severity = str(advisory.severity or "unknown").lower()
        server["advisories"].append(
            {
                "advisory_id": advisory.id,
                "feed": advisory.feed,
                "summary": advisory.summary,
                "severity": severity,
                "package": advisory.package,
                "match_basis": link.match_basis,
                "match_confidence": link.match_confidence,
                "published_at": advisory.published_at,
                "source_url": advisory.source_url,
            }
        )
        if severity in server["counts_by_severity"]:
            server["counts_by_severity"][severity] += 1
        total_links += 1

    return {
        "servers": list(servers.values()),
        "total_links": total_links,
    }


def advisory_severity_map(
    db: Session,
    server_id: Optional[str] = None,
    ecosystem: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Compatibility name for callers that use the shorter service function name."""
    return get_advisory_severity_map(
        db,
        server_id=server_id,
        ecosystem=ecosystem,
        min_confidence=min_confidence,
    )


def build_advisory_severity_map(
    db: Session,
    server_id: Optional[str] = None,
    ecosystem: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Compatibility name for callers that use the builder-style function name."""
    return get_advisory_severity_map(
        db,
        server_id=server_id,
        ecosystem=ecosystem,
        min_confidence=min_confidence,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    session.add(
        VulnAdvisory(
            id="adv-critical",
            feed="test-feed",
            summary="critical advisory",
            severity="critical",
            ecosystem="npm",
            package="left-pad",
            source_url="https://example.test/adv-critical",
        )
    )
    session.add(
        VulnAdvisory(
            id="adv-high",
            feed="test-feed",
            summary="high advisory",
            severity="high",
            ecosystem="npm",
            package="express",
            source_url="https://example.test/adv-high",
        )
    )
    session.add(
        VulnAdvisory(
            id="adv-medium",
            feed="test-feed",
            summary="medium advisory",
            severity="medium",
            ecosystem="npm",
            package="lodash",
            source_url="https://example.test/adv-medium",
        )
    )
    session.add(
        VulnLink(
            id=1,
            advisory_id="adv-critical",
            server_id="server-1",
            match_basis="package",
            match_value="left-pad",
            match_confidence=0.99,
        )
    )
    session.add(
        VulnLink(
            id=2,
            advisory_id="adv-high",
            server_id="server-1",
            match_basis="package",
            match_value="express",
            match_confidence=0.90,
        )
    )
    session.add(
        VulnLink(
            id=3,
            advisory_id="adv-medium",
            server_id="server-2",
            match_basis="package",
            match_value="lodash",
            match_confidence=0.20,
        )
    )
    session.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/advisory-severity-map")
    def test_advisory_severity_map(
        server_id: str = "server-1",
        ecosystem: str = "npm",
        min_confidence: Optional[float] = None,
    ):
        return get_advisory_severity_map(
            session,
            server_id=server_id,
            ecosystem=ecosystem,
            min_confidence=min_confidence,
        )

    test_app.dependency_overrides[get_session] = lambda: session

    result = get_advisory_severity_map(
        session,
        server_id="server-1",
        ecosystem="npm",
    )
    assert result["total_links"] == 2
    assert result["servers"][0]["counts_by_severity"]["critical"] == 1
    assert result["servers"][0]["counts_by_severity"]["high"] == 1

    filtered = get_advisory_severity_map(
        session,
        server_id="server-2",
        ecosystem="npm",
        min_confidence=0.5,
    )
    assert filtered["total_links"] == 0

    print("PASS")