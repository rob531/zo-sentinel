from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class SeverityMix(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class EcosystemCoverage(BaseModel):
    ecosystem: str
    advisory_count: int
    package_count: int
    feed_count: int
    linked_servers: int
    severity: SeverityMix


class CoverageTotals(BaseModel):
    advisories: int
    ecosystems: int
    linked_servers: int


class EcosystemCoverageResponse(BaseModel):
    days: int
    generated_at: datetime
    ecosystems: list[EcosystemCoverage]
    totals: CoverageTotals


_COVERAGE_SQL = """
WITH filtered_advisories AS (
    SELECT
        id,
        COALESCE(ecosystem, 'unknown') AS ecosystem,
        package,
        feed,
        LOWER(TRIM(COALESCE(severity, ''))) AS severity
    FROM vuln_advisories
    WHERE published_at >= :cutoff
),
advisory_rollup AS (
    SELECT
        ecosystem,
        COUNT(DISTINCT id) AS advisory_count,
        COUNT(DISTINCT package) AS package_count,
        COUNT(DISTINCT feed) AS feed_count,
        COUNT(DISTINCT CASE WHEN severity = 'critical' THEN id END) AS critical,
        COUNT(DISTINCT CASE WHEN severity = 'high' THEN id END) AS high,
        COUNT(DISTINCT CASE WHEN severity = 'medium' THEN id END) AS medium,
        COUNT(DISTINCT CASE WHEN severity = 'low' THEN id END) AS low
    FROM filtered_advisories
    GROUP BY ecosystem
),
linked_rollup AS (
    SELECT
        fa.ecosystem,
        COUNT(DISTINCT vl.server_id) AS linked_servers
    FROM filtered_advisories AS fa
    INNER JOIN vuln_links AS vl ON vl.advisory_id = fa.id
    GROUP BY fa.ecosystem
)
SELECT
    ar.ecosystem,
    ar.advisory_count,
    ar.package_count,
    ar.feed_count,
    COALESCE(lr.linked_servers, 0) AS linked_servers,
    ar.critical,
    ar.high,
    ar.medium,
    ar.low
FROM advisory_rollup AS ar
LEFT JOIN linked_rollup AS lr ON lr.ecosystem = ar.ecosystem
ORDER BY ar.ecosystem
"""

_TOTALS_SQL = """
WITH filtered_advisories AS (
    SELECT id
    FROM vuln_advisories
    WHERE published_at >= :cutoff
)
SELECT
    COUNT(DISTINCT fa.id) AS advisories,
    COUNT(DISTINCT vl.server_id) AS linked_servers
FROM filtered_advisories AS fa
LEFT JOIN vuln_links AS vl ON vl.advisory_id = fa.id
"""


router = APIRouter(prefix="/api")


@router.get("/advisories/ecosystem-coverage", response_model=EcosystemCoverageResponse)
def ecosystem_coverage(
    days: int = Query(default=90, ge=0, le=3650),
    session: Session = Depends(get_session),
) -> EcosystemCoverageResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(text(_COVERAGE_SQL), {"cutoff": cutoff}).mappings().all()
    totals = session.execute(text(_TOTALS_SQL), {"cutoff": cutoff}).mappings().one()

    ecosystems = [
        EcosystemCoverage(
            ecosystem=row["ecosystem"],
            advisory_count=row["advisory_count"],
            package_count=row["package_count"],
            feed_count=row["feed_count"],
            linked_servers=row["linked_servers"],
            severity=SeverityMix(
                critical=row["critical"],
                high=row["high"],
                medium=row["medium"],
                low=row["low"],
            ),
        )
        for row in rows
    ]

    return EcosystemCoverageResponse(
        days=days,
        generated_at=datetime.now(timezone.utc),
        ecosystems=ecosystems,
        totals=CoverageTotals(
            advisories=totals["advisories"],
            ecosystems=len(ecosystems),
            linked_servers=totals["linked_servers"],
        ),
    )


def _seed_test_database(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed VARCHAR NOT NULL,
                    summary TEXT,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    package VARCHAR,
                    affected_ranges JSON,
                    aliases JSON,
                    source_url VARCHAR NOT NULL,
                    published_at DATETIME,
                    fetched_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    identities JSON,
                    content_hash VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER NOT NULL,
                    server_id VARCHAR NOT NULL,
                    match_basis VARCHAR NOT NULL,
                    match_value VARCHAR NOT NULL,
                    match_confidence VARCHAR NOT NULL,
                    linked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

        published_at = datetime.now(timezone.utc) - timedelta(days=10)
        advisories = [
            {
                "id": 1,
                "feed": "osv",
                "summary": "npm critical advisory",
                "severity": "critical",
                "ecosystem": "npm",
                "package": "alpha",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.test/advisories/1",
                "published_at": published_at,
                "identities": "[]",
                "content_hash": "hash-1",
            },
            {
                "id": 2,
                "feed": "github",
                "summary": "npm high advisory",
                "severity": "high",
                "ecosystem": "npm",
                "package": "beta",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.test/advisories/2",
                "published_at": published_at,
                "identities": "[]",
                "content_hash": "hash-2",
            },
            {
                "id": 3,
                "feed": "osv",
                "summary": "pypi low advisory",
                "severity": "low",
                "ecosystem": "pypi",
                "package": "gamma",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.test/advisories/3",
                "published_at": published_at,
                "identities": "[]",
                "content_hash": "hash-3",
            },
            {
                "id": 4,
                "feed": "github",
                "summary": "pypi medium advisory",
                "severity": "medium",
                "ecosystem": "pypi",
                "package": "delta",
                "affected_ranges": "[]",
                "aliases": "[]",
                "source_url": "https://example.test/advisories/4",
                "published_at": published_at,
                "identities": "[]",
                "content_hash": "hash-4",
            },
        ]
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, summary, severity, ecosystem, package,
                    affected_ranges, aliases, source_url, published_at,
                    identities, content_hash
                ) VALUES (
                    :id, :feed, :summary, :severity, :ecosystem, :package,
                    :affected_ranges, :aliases, :source_url, :published_at,
                    :identities, :content_hash
                )
                """
            ),
            advisories,
        )

        links = [
            {
                "id": 1,
                "advisory_id": 1,
                "server_id": "server-alpha",
                "match_basis": "package",
                "match_value": "alpha",
                "match_confidence": "high",
            },
            {
                "id": 2,
                "advisory_id": 2,
                "server_id": "server-beta",
                "match_basis": "package",
                "match_value": "beta",
                "match_confidence": "medium",
            },
            {
                "id": 3,
                "advisory_id": 3,
                "server_id": "server-alpha",
                "match_basis": "package",
                "match_value": "gamma",
                "match_confidence": "low",
            },
        ]
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (
                    id, advisory_id, server_id, match_basis,
                    match_value, match_confidence
                ) VALUES (
                    :id, :advisory_id, :server_id, :match_basis,
                    :match_value, :match_confidence
                )
                """
            ),
            links,
        )


def run_contract() -> dict:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def get_test_session() -> Iterator[Session]:
        with test_session_factory() as session:
            yield session

    _seed_test_database(engine)

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = get_test_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/advisories/ecosystem-coverage", params={"days": 90})
            assert response.status_code == 200, response.text
            payload = response.json()

        ecosystems = payload["ecosystems"]
        by_name = {item["ecosystem"]: item for item in ecosystems}
        assert "npm" in by_name
        assert "pypi" in by_name
        assert by_name["npm"]["advisory_count"] == 2
        assert by_name["npm"]["linked_servers"] >= 1
        assert by_name["npm"]["severity"]["critical"] == 1
        assert by_name["npm"]["severity"]["high"] == 1
        assert by_name["pypi"]["advisory_count"] == 2
        assert payload["totals"] == {
            "advisories": 4,
            "ecosystems": 2,
            "linked_servers": 2,
        }
        return payload
    finally:
        engine.dispose()


if __name__ == "__main__":
    run_contract()
    print("PASS")