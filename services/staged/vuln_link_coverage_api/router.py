from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api", tags=["vuln-link-coverage"])


class MatchBasisCoverage(BaseModel):
    match_basis: str
    link_count: int
    avg_confidence: float
    servers: int


class SeverityCoverage(BaseModel):
    severity: str
    link_count: int


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    linked_servers: int
    link_count: int
    max_match_confidence: float


class CoverageTotals(BaseModel):
    links: int
    linked_servers: int
    distinct_advisories: int


class VulnLinkCoverageResponse(BaseModel):
    generated_at: str
    days: int
    totals: CoverageTotals
    by_match_basis: list[MatchBasisCoverage]
    by_severity: list[SeverityCoverage]
    by_registry_source: list[RegistrySourceCoverage]
    unlinked_servers: int


def confidence_bucket(v: float) -> str:
    if v < 0.5:
        return "lt_0.5"
    if v < 0.75:
        return "0.5_0.75"
    return "gte_0.75"


@router.get(
    "/vuln/link/coverage",
    response_model=VulnLinkCoverageResponse,
    summary="Measure vulnerability-link coverage for MCP registry servers",
)
def vuln_link_coverage(
    registry_source: Optional[str] = Query(
        default=None, description="Optional registry source to filter by"
    ),
    days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> VulnLinkCoverageResponse:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    statement = (
        select(
            VulnLink.id,
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_confidence,
            VulnAdvisory.severity,
            McpServerRegistry.registry_source,
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .where(VulnLink.linked_at >= cutoff)
    )

    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )

    rows = session.execute(statement).all()

    registry_statement = select(McpServerRegistry.server_id)
    if registry_source is not None:
        registry_statement = registry_statement.where(
            McpServerRegistry.registry_source == registry_source
        )
    registry_server_ids = {
        row[0] for row in session.execute(registry_statement).all()
    }

    linked_server_ids = set()
    advisory_ids = set()
    match_basis_links: dict[str, int] = defaultdict(int)
    match_basis_confidence: dict[str, list[float]] = defaultdict(list)
    match_basis_servers: dict[str, set[str]] = defaultdict(set)
    severity_links: dict[str, int] = defaultdict(int)
    source_links: dict[str, int] = defaultdict(int)
    source_servers: dict[str, set[str]] = defaultdict(set)
    source_confidence: dict[str, list[float]] = defaultdict(list)

    for row in rows:
        _, advisory_id, server_id, match_basis, confidence, severity, source = row
        confidence_value = float(confidence or 0.0)
        source_value = str(source) if source is not None else "unknown"
        basis_value = str(match_basis) if match_basis is not None else "unknown"
        severity_value = str(severity) if severity is not None else "unknown"

        linked_server_ids.add(server_id)
        advisory_ids.add(advisory_id)
        match_basis_links[basis_value] += 1
        match_basis_confidence[basis_value].append(confidence_value)
        match_basis_servers[basis_value].add(server_id)
        severity_links[severity_value] += 1
        source_links[source_value] += 1
        source_servers[source_value].add(server_id)
        source_confidence[source_value].append(confidence_value)

    by_match_basis = []
    for basis in sorted(match_basis_links):
        confidence_values = match_basis_confidence[basis]
        average_confidence = (
            round(sum(confidence_values) / len(confidence_values), 2)
            if confidence_values
            else 0.0
        )
        by_match_basis.append(
            MatchBasisCoverage(
                match_basis=basis,
                link_count=match_basis_links[basis],
                avg_confidence=average_confidence,
                servers=len(match_basis_servers[basis]),
            )
        )

    by_severity = [
        SeverityCoverage(severity=severity, link_count=severity_links[severity])
        for severity in sorted(severity_links)
    ]

    by_registry_source = []
    for source in sorted(source_links):
        confidence_values = source_confidence[source]
        by_registry_source.append(
            RegistrySourceCoverage(
                registry_source=source,
                linked_servers=len(source_servers[source]),
                link_count=source_links[source],
                max_match_confidence=(
                    round(max(confidence_values), 2)
                    if confidence_values
                    else 0.0
                ),
            )
        )

    unlinked_servers = len(registry_server_ids - linked_server_ids)

    return VulnLinkCoverageResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        days=days,
        totals=CoverageTotals(
            links=len(rows),
            linked_servers=len(linked_server_ids),
            distinct_advisories=len(advisory_ids),
        ),
        by_match_basis=by_match_basis,
        by_severity=by_severity,
        by_registry_source=by_registry_source,
        unlinked_servers=unlinked_servers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    published_at DATETIME
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
                    match_confidence REAL,
                    linked_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    risk_tier VARCHAR
                )
                """
            )
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, severity, ecosystem, published_at)
                VALUES
                    ('adv-1', 'high', 'npm', :published_at),
                    ('adv-2', 'critical', 'pypi', :published_at)
                """
            ),
            {"published_at": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 'adv-1', 'server-1', 'package', 'pkg-a', 0.9, :linked_at),
                    (2, 'adv-1', 'server-1', 'direct', 'direct-a', 0.4, :linked_at),
                    (3, 'adv-2', 'server-2', 'package', 'pkg-b', 0.9, :linked_at),
                    (4, 'adv-2', 'server-2', 'direct', 'direct-b', 0.4, :linked_at)
                """
            ),
            {"linked_at": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, risk_tier)
                VALUES
                    ('server-1', 'One', 'alpha', 'high'),
                    ('server-2', 'Two', 'beta', 'medium'),
                    ('server-3', 'Three', 'alpha', 'low')
                """
            )
        )

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/vuln/link/coverage?days=30")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["totals"]["links"] == 4
    assert payload["totals"]["linked_servers"] == 2
    assert payload["totals"]["distinct_advisories"] == 2
    assert payload["unlinked_servers"] == 1
    assert len(payload["by_match_basis"]) == 2
    print("PASS")