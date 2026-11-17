from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


SEVERITIES = ("critical", "high", "medium", "low", "unknown")


class AdvisoryExposureItem(BaseModel):
    advisory_id: str
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    match_basis: str
    match_value: str
    match_confidence: float
    source_url: str
    published_at: str


class AdvisoryExposureResponse(BaseModel):
    server_id: str
    total: int
    severity_counts: Dict[str, int]
    advisories: List[AdvisoryExposureItem]


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _published_at(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _text(value)


def list_advisory_exposure(
    session: Session, server_id: str
) -> AdvisoryExposureResponse:
    rows = session.execute(
        select(VulnAdvisory, VulnLink)
        .select_from(VulnLink)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.server_id == server_id)
        .order_by(VulnAdvisory.published_at.desc(), VulnAdvisory.id.asc())
    ).all()

    severity_counts = {severity: 0 for severity in SEVERITIES}
    advisories: List[AdvisoryExposureItem] = []

    for advisory, link in rows:
        severity = _text(advisory.severity).strip().lower() or "unknown"
        if severity not in severity_counts:
            severity = "unknown"
        severity_counts[severity] += 1

        advisories.append(
            AdvisoryExposureItem(
                advisory_id=_text(advisory.id),
                feed=_text(advisory.feed),
                summary=_text(advisory.summary),
                severity=severity,
                ecosystem=_text(advisory.ecosystem),
                package=_text(advisory.package),
                match_basis=_text(link.match_basis),
                match_value=_text(link.match_value),
                match_confidence=float(link.match_confidence or 0.0),
                source_url=_text(advisory.source_url),
                published_at=_published_at(advisory.published_at),
            )
        )

    return AdvisoryExposureResponse(
        server_id=str(server_id),
        total=len(advisories),
        severity_counts=severity_counts,
        advisories=advisories,
    )


def create_contract_app() -> FastAPI:
    application = FastAPI(title="Server Advisory Exposure API")

    @application.get(
        "/api/servers/{server_id}/advisory-exposure",
        response_model=AdvisoryExposureResponse,
    )
    def advisory_exposure(
        server_id: str, session: Session = Depends(get_session)
    ) -> AdvisoryExposureResponse:
        return list_advisory_exposure(session, server_id)

    return application


app = create_contract_app()


def _create_sqlite_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    feed VARCHAR,
                    summary TEXT,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    package VARCHAR,
                    affected_ranges TEXT,
                    aliases TEXT,
                    content_hash VARCHAR,
                    identities TEXT,
                    fetched_at DATETIME,
                    source_url VARCHAR,
                    published_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id VARCHAR PRIMARY KEY,
                    advisory_id VARCHAR,
                    server_id VARCHAR,
                    match_basis VARCHAR,
                    match_value VARCHAR,
                    match_confidence FLOAT,
                    linked_at DATETIME
                )
                """
            )
        )

    return engine


def _seed_store(session: Session) -> None:
    session.execute(
        text(
            """
            INSERT INTO vuln_advisories (
                id, feed, summary, severity, ecosystem, package,
                affected_ranges, aliases, content_hash, identities,
                fetched_at, source_url, published_at
            ) VALUES (
                :id, :feed, :summary, :severity, :ecosystem, :package,
                NULL, NULL, NULL, NULL,
                :fetched_at, :source_url, :published_at
            )
            """
        ),
        [
            {
                "id": "advisory-critical",
                "feed": "osv",
                "summary": "Critical advisory",
                "severity": "critical",
                "ecosystem": "pypi",
                "package": "critical-package",
                "fetched_at": datetime(2024, 1, 3),
                "source_url": "https://example.test/advisories/critical",
                "published_at": datetime(2024, 1, 3),
            },
            {
                "id": "advisory-high",
                "feed": "osv",
                "summary": "High advisory",
                "severity": "high",
                "ecosystem": "npm",
                "package": "high-package",
                "fetched_at": datetime(2024, 1, 2),
                "source_url": "https://example.test/advisories/high",
                "published_at": datetime(2024, 1, 2),
            },
            {
                "id": "advisory-unknown",
                "feed": "vendor",
                "summary": "Unclassified advisory",
                "severity": "unknown",
                "ecosystem": "go",
                "package": "unknown-package",
                "fetched_at": datetime(2024, 1, 1),
                "source_url": "https://example.test/advisories/unknown",
                "published_at": datetime(2024, 1, 1),
            },
        ],
    )

    session.execute(
        text(
            """
            INSERT INTO vuln_links (
                id, advisory_id, server_id, match_basis,
                match_value, match_confidence, linked_at
            ) VALUES (
                :id, :advisory_id, :server_id, :match_basis,
                :match_value, :match_confidence, :linked_at
            )
            """
        ),
        [
            {
                "id": "link-critical",
                "advisory_id": "advisory-critical",
                "server_id": "linked-server",
                "match_basis": "package",
                "match_value": "critical-package",
                "match_confidence": 0.99,
                "linked_at": datetime(2024, 1, 3),
            },
            {
                "id": "link-unknown",
                "advisory_id": "advisory-unknown",
                "server_id": "linked-server",
                "match_basis": "package",
                "match_value": "unknown-package",
                "match_confidence": 0.91,
                "linked_at": datetime(2024, 1, 2),
            },
            {
                "id": "link-high",
                "advisory_id": "advisory-high",
                "server_id": "other-linked-server",
                "match_basis": "package",
                "match_value": "high-package",
                "match_confidence": 0.95,
                "linked_at": datetime(2024, 1, 1),
            },
        ],
    )
    session.commit()


def main() -> None:
    engine = _create_sqlite_store()
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session = test_session_factory()
    try:
        _seed_store(session)
    finally:
        session.close()

    that_app = create_contract_app()

    def override_get_session():
        override_session = test_session_factory()
        try:
            yield override_session
        finally:
            override_session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/servers/linked-server/advisory-exposure"
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["server_id"] == "linked-server"
        assert payload["total"] == 2
        assert payload["severity_counts"]["critical"] == 1
        assert payload["severity_counts"]["unknown"] == 1
        assert set(payload["severity_counts"]) == set(SEVERITIES)
        assert len(payload["advisories"]) == 2

        unlinked_response = client.get(
            "/api/servers/unlinked-server/advisory-exposure"
        )
        assert unlinked_response.status_code == 200, unlinked_response.text
        unlinked_payload = unlinked_response.json()
        assert unlinked_payload["total"] == 0
        assert unlinked_payload["advisories"] == []
        assert set(unlinked_payload["severity_counts"]) == set(SEVERITIES)

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    main()