from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["vuln-links"])


class VulnLinkItem(BaseModel):
    link_id: int
    advisory_id: int
    feed: str
    summary: Optional[str] = None
    severity: str
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: datetime
    published_at: Optional[datetime] = None
    source_url: str
    content_hash: Optional[str] = None


class VulnLinkResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    current_tier: Optional[str] = None
    total: int
    by_severity: Dict[str, int] = Field(default_factory=dict)
    by_basis: Dict[str, int] = Field(default_factory=dict)
    links: List[VulnLinkItem] = Field(default_factory=list)


_SEVERITIES = ("critical", "high", "medium", "low", "unknown")

_LINK_SQL = text(
    """
    SELECT
        vl.id AS link_id,
        va.id AS advisory_id,
        va.feed,
        va.summary,
        va.severity,
        va.ecosystem,
        va.package,
        va.affected_ranges,
        va.aliases,
        va.source_url,
        va.published_at,
        va.fetched_at,
        va.content_hash,
        vl.match_basis,
        vl.match_value,
        vl.match_confidence,
        vl.linked_at
    FROM vuln_links AS vl
    INNER JOIN vuln_advisories AS va
        ON va.id = vl.advisory_id
    LEFT JOIN mcp_server_registry AS msr
        ON msr.server_id = vl.server_id
    WHERE vl.server_id = :server_id
      AND vl.match_confidence >= :min_confidence
    ORDER BY vl.match_confidence DESC, vl.linked_at DESC, vl.id ASC
    LIMIT :limit
    """
)

_SERVER_SQL = text(
    """
    SELECT name, risk_tier
    FROM mcp_server_registry
    WHERE server_id = :server_id
    """
)


def _severity_bucket(value: object) -> str:
    severity = str(value or "unknown").strip().lower()
    return severity if severity in _SEVERITIES else "unknown"


def get_vuln_link_chain(
    session: Session,
    server_id: str,
    min_confidence: float = 0.0,
    limit: int = 100,
) -> VulnLinkResponse:
    server_row = session.execute(
        _SERVER_SQL,
        {"server_id": server_id},
    ).mappings().first()

    rows = session.execute(
        _LINK_SQL,
        {
            "server_id": server_id,
            "min_confidence": min_confidence,
            "limit": limit,
        },
    ).mappings().all()

    by_severity = {severity: 0 for severity in _SEVERITIES}
    by_basis: Dict[str, int] = {}
    links: List[VulnLinkItem] = []

    for row in rows:
        severity = _severity_bucket(row["severity"])
        basis = str(row["match_basis"])

        by_severity[severity] += 1
        by_basis[basis] = by_basis.get(basis, 0) + 1

        links.append(
            VulnLinkItem(
                link_id=row["link_id"],
                advisory_id=row["advisory_id"],
                feed=row["feed"],
                summary=row["summary"],
                severity=severity,
                ecosystem=row["ecosystem"],
                package=row["package"],
                match_basis=basis,
                match_value=row["match_value"],
                match_confidence=float(row["match_confidence"]),
                linked_at=row["linked_at"],
                published_at=row["published_at"],
                source_url=row["source_url"],
                content_hash=row["content_hash"],
            )
        )

    return VulnLinkResponse(
        server_id=server_id,
        server_name=server_row["name"] if server_row else None,
        current_tier=server_row["risk_tier"] if server_row else None,
        total=len(links),
        by_severity=by_severity,
        by_basis=by_basis,
        links=links,
    )


@router.get(
    "/servers/{server_id}/vuln-links",
    response_model=VulnLinkResponse,
)
def list_vuln_links(
    server_id: str,
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> VulnLinkResponse:
    return get_vuln_link_chain(
        session=session,
        server_id=server_id,
        min_confidence=min_confidence,
        limit=limit,
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(
        engine,
        tables=[
            McpServerRegistry.__table__,
            VulnAdvisory.__table__,
            VulnLink.__table__,
        ],
    )
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    db = testing_session()
    try:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-seeded",
                    name="Seeded MCP Server",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-other",
                    name="Other MCP Server",
                    risk_tier="medium",
                ),
                VulnAdvisory(
                    id=1001,
                    feed="osv",
                    summary="First advisory",
                    severity="critical",
                    ecosystem="pypi",
                    package="demo-package",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=["CVE-2025-0001"],
                    source_url="https://example.test/advisories/1001",
                    published_at=now,
                    fetched_at=now,
                    content_hash="hash-1001",
                ),
                VulnAdvisory(
                    id=1002,
                    feed="github",
                    summary="Second advisory",
                    severity="medium",
                    ecosystem="npm",
                    package="demo-package",
                    affected_ranges=[{"introduced": "1.0"}],
                    aliases=["GHSA-test-0002"],
                    source_url="https://example.test/advisories/1002",
                    published_at=now,
                    fetched_at=now,
                    content_hash="hash-1002",
                ),
                VulnLink(
                    id=1,
                    advisory_id=1001,
                    server_id="server-seeded",
                    match_basis="package_name",
                    match_value="demo-package",
                    match_confidence=0.95,
                    linked_at=now,
                ),
                VulnLink(
                    id=2,
                    advisory_id=1002,
                    server_id="server-seeded",
                    match_basis="repository_url",
                    match_value="https://example.test/repository",
                    match_confidence=0.82,
                    linked_at=now,
                ),
                VulnLink(
                    id=3,
                    advisory_id=1001,
                    server_id="server-other",
                    match_basis="package_name",
                    match_value="demo-package",
                    match_confidence=0.99,
                    linked_at=now,
                ),
            ]
        )
        db.commit()

        def override_get_session():
            try:
                yield db
            finally:
                db.close()

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_get_session
        that_app.include_router(router)

        with TestClient(that_app) as client:
            response = client.get("/api/servers/server-seeded/vuln-links")

        assert response.status_code == 200
        payload = response.json()
        assert len(payload["links"]) == 2
        assert payload["total"] == 2
        assert sum(payload["by_severity"].values()) == 2
        assert payload["by_severity"]["critical"] == 1
        assert payload["by_severity"]["medium"] == 1
        assert payload["by_basis"]["package_name"] == 1
        assert payload["by_basis"]["repository_url"] == 1
        assert payload["server_name"] == "Seeded MCP Server"
        assert payload["current_tier"] == "high"
        assert all("match_basis" in item for item in payload["links"])
        assert all("match_confidence" in item for item in payload["links"])
        assert all(item["advisory_id"] != 1001 or item["link_id"] != 3 for item in payload["links"])
    finally:
        db.rollback()
        db.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()