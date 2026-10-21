from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

from .logic import link_rows, summarize

router = APIRouter(prefix="/api/vuln", tags=["vulnerability-link-provenance"])


class VulnLinkResponse(BaseModel):
    id: int
    advisory_id: str
    server_id: str
    match_basis: str
    match_value: Optional[str] = None
    match_confidence: Optional[float] = None
    linked_at: Optional[datetime] = None
    feed: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    server_name: Optional[str] = None
    risk_tier: Optional[str] = None


class SeverestAdvisoryResponse(BaseModel):
    id: Optional[str] = None
    summary: Optional[str] = None
    severity: Optional[str] = None


class VulnLinkSummaryResponse(BaseModel):
    server_id: str
    link_count: int
    max_confidence: Optional[float] = None
    by_match_basis: dict[str, int]
    by_severity: dict[str, int]
    severest_advisory: Optional[SeverestAdvisoryResponse] = None


def _load_links(
    session: Session,
    server_id: Optional[str] = None,
    advisory_id: Optional[str] = None,
    min_confidence: Optional[float] = None,
    limit: Optional[int] = None,
) -> List[dict]:
    statement = select(
        VulnLink.id,
        VulnLink.advisory_id,
        VulnLink.server_id,
        VulnLink.match_basis,
        VulnLink.match_value,
        VulnLink.match_confidence,
        VulnLink.linked_at,
    ).order_by(VulnLink.id.desc())

    if server_id is not None:
        statement = statement.where(VulnLink.server_id == server_id)
    if advisory_id is not None:
        statement = statement.where(VulnLink.advisory_id == advisory_id)
    if min_confidence is not None:
        statement = statement.where(VulnLink.match_confidence >= min_confidence)
    if limit is not None:
        statement = statement.limit(limit)

    raw_links = [dict(row) for row in session.execute(statement).mappings().all()]
    advisory_ids = {row["advisory_id"] for row in raw_links}
    server_ids = {row["server_id"] for row in raw_links}

    advisory_rows: List[dict] = []
    if advisory_ids:
        advisory_statement = select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
        ).where(VulnAdvisory.id.in_(advisory_ids))
        advisory_rows = [
            dict(row) for row in session.execute(advisory_statement).mappings().all()
        ]

    registry_rows: List[dict] = []
    if server_ids:
        registry_statement = select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).where(McpServerRegistry.server_id.in_(server_ids))
        registry_rows = [
            dict(row) for row in session.execute(registry_statement).mappings().all()
        ]

    return link_rows(raw_links, advisory_rows, registry_rows)


@router.get("/links", response_model=List[VulnLinkResponse])
def get_vuln_links(
    server_id: Optional[str] = Query(None),
    advisory_id: Optional[str] = Query(None),
    min_confidence: Optional[float] = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> List[dict]:
    return _load_links(
        session,
        server_id=server_id,
        advisory_id=advisory_id,
        min_confidence=min_confidence,
        limit=limit,
    )


@router.get("/links/{server_id}/summary", response_model=VulnLinkSummaryResponse)
def get_vuln_link_summary(
    server_id: str,
    session: Session = Depends(get_session),
) -> dict:
    rows = _load_links(session, server_id=server_id)
    return summarize(server_id, rows)


if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    feed VARCHAR,
                    summary VARCHAR,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    package VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
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
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, name, risk_tier)
                VALUES ('server-1', 'Example MCP Server', 'HIGH')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, summary, severity, ecosystem, package)
                VALUES
                    ('ADV-CRITICAL', 'osv', 'Critical advisory', 'CRITICAL', 'npm', 'pkg-a'),
                    ('ADV-MEDIUM', 'osv', 'Medium advisory', 'MEDIUM', 'npm', 'pkg-b')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 'ADV-CRITICAL', 'server-1', 'package_name', 'pkg-a',
                     0.99, '2026-01-01T00:00:00+00:00'),
                    (2, 'ADV-MEDIUM', 'server-1', 'cpe', 'cpe:/a:example/pkg-b',
                     0.90, '2026-01-02T00:00:00+00:00'),
                    (3, 'ADV-CRITICAL', 'server-1', 'exact_version', '1.2.3',
                     0.95, '2026-01-03T00:00:00+00:00')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as test_session:
            yield test_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        links_response = client.get("/api/vuln/links")
        assert links_response.status_code == 200
        links = links_response.json()
        assert len(links) == 3
        assert all(row["severity"] in {"CRITICAL", "MEDIUM"} for row in links)

        summary_response = client.get("/api/vuln/links/server-1/summary")
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert summary["link_count"] == 3
        assert {"package_name", "cpe", "exact_version"} <= set(
            summary["by_match_basis"]
        )
        assert summary["by_severity"]["CRITICAL"] == 2
        assert summary["by_severity"]["MEDIUM"] == 1

    print("PASS")