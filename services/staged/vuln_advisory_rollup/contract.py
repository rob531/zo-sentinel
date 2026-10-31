from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


ADVISORY_TABLE = VulnAdvisory.__tablename__
LINK_TABLE = VulnLink.__tablename__

SEVERITIES = ("critical", "high", "medium", "low", "unknown")


class RollupFilters(BaseModel):
    ecosystem: Optional[str] = None
    severity: Optional[str] = None
    days: int


class SeverityRollup(BaseModel):
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0


class EcosystemRollup(BaseModel):
    ecosystem: str
    advisory_count: int
    linked_server_count: int


class TopLinkedAdvisory(BaseModel):
    advisory_id: int
    severity: str
    ecosystem: str
    package: Optional[str] = None
    server_count: int


class AdvisoryRollupResponse(BaseModel):
    filters: RollupFilters
    by_severity: SeverityRollup
    by_ecosystem: list[EcosystemRollup] = Field(default_factory=list)
    top_linked: list[TopLinkedAdvisory] = Field(default_factory=list)
    window_days: int


router = APIRouter(prefix="/api")


@router.get(
    "/vuln/advisories/rollup",
    response_model=AdvisoryRollupResponse,
)
def advisory_rollup(
    ecosystem: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=0),
    session: Session = Depends(get_session),
) -> AdvisoryRollupResponse:
    ecosystem_filter = ecosystem.strip().lower() if ecosystem and ecosystem.strip() else None
    severity_filter = severity.strip().lower() if severity and severity.strip() else None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    advisory_sql = text(
        f"""
        SELECT
            a.id AS advisory_id,
            COALESCE(NULLIF(lower(a.severity), ''), 'unknown') AS severity,
            COALESCE(NULLIF(lower(a.ecosystem), ''), 'unknown') AS ecosystem,
            a.package AS package,
            COUNT(DISTINCT l.server_id) AS server_count
        FROM {ADVISORY_TABLE} AS a
        LEFT JOIN {LINK_TABLE} AS l
            ON l.advisory_id = a.id
        WHERE (
            :ecosystem IS NULL
            OR COALESCE(NULLIF(lower(a.ecosystem), ''), 'unknown') = :ecosystem
        )
        AND (
            :severity IS NULL
            OR COALESCE(NULLIF(lower(a.severity), ''), 'unknown') = :severity
        )
        AND a.published_at >= :cutoff
        GROUP BY
            a.id,
            COALESCE(NULLIF(lower(a.severity), ''), 'unknown'),
            COALESCE(NULLIF(lower(a.ecosystem), ''), 'unknown'),
            a.package
        ORDER BY server_count DESC, a.id ASC
        """
    )

    ecosystem_link_sql = text(
        f"""
        SELECT
            COALESCE(NULLIF(lower(a.ecosystem), ''), 'unknown') AS ecosystem,
            COUNT(DISTINCT l.server_id) AS linked_server_count
        FROM {ADVISORY_TABLE} AS a
        INNER JOIN {LINK_TABLE} AS l
            ON l.advisory_id = a.id
        WHERE (
            :ecosystem IS NULL
            OR COALESCE(NULLIF(lower(a.ecosystem), ''), 'unknown') = :ecosystem
        )
        AND (
            :severity IS NULL
            OR COALESCE(NULLIF(lower(a.severity), ''), 'unknown') = :severity
        )
        AND a.published_at >= :cutoff
        GROUP BY COALESCE(NULLIF(lower(a.ecosystem), ''), 'unknown')
        """
    )

    parameters = {
        "ecosystem": ecosystem_filter,
        "severity": severity_filter,
        "cutoff": cutoff,
    }

    advisory_rows = list(session.execute(advisory_sql, parameters).mappings())
    ecosystem_link_rows = {
        row["ecosystem"]: int(row["linked_server_count"])
        for row in session.execute(ecosystem_link_sql, parameters).mappings()
    }

    severity_counts = {name: 0 for name in SEVERITIES}
    ecosystem_counts: dict[str, int] = {}

    for row in advisory_rows:
        normalized_severity = row["severity"]
        if normalized_severity not in severity_counts:
            normalized_severity = "unknown"
        severity_counts[normalized_severity] += 1

        normalized_ecosystem = row["ecosystem"]
        ecosystem_counts[normalized_ecosystem] = (
            ecosystem_counts.get(normalized_ecosystem, 0) + 1
        )

    by_ecosystem = [
        EcosystemRollup(
            ecosystem=name,
            advisory_count=count,
            linked_server_count=ecosystem_link_rows.get(name, 0),
        )
        for name, count in sorted(ecosystem_counts.items())
    ]

    top_linked = [
        TopLinkedAdvisory(
            advisory_id=int(row["advisory_id"]),
            severity=row["severity"]
            if row["severity"] in SEVERITIES
            else "unknown",
            ecosystem=row["ecosystem"],
            package=row["package"],
            server_count=int(row["server_count"]),
        )
        for row in advisory_rows
        if int(row["server_count"]) > 0
    ]

    return AdvisoryRollupResponse(
        filters=RollupFilters(
            ecosystem=ecosystem,
            severity=severity,
            days=days,
        ),
        by_severity=SeverityRollup(**severity_counts),
        by_ecosystem=by_ecosystem,
        top_linked=top_linked,
        window_days=days,
    )


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

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
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url VARCHAR NOT NULL,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP NOT NULL,
                    identities TEXT,
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
                    match_confidence FLOAT NOT NULL,
                    linked_at TIMESTAMP NOT NULL
                )
                """
            )
        )

    now = datetime.now(timezone.utc)

    with test_session_factory() as session:
        session.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, summary, severity, ecosystem, package,
                    affected_ranges, aliases, source_url, published_at,
                    fetched_at, identities, content_hash
                ) VALUES (
                    :id, :feed, :summary, :severity, :ecosystem, :package,
                    :affected_ranges, :aliases, :source_url, :published_at,
                    :fetched_at, :identities, :content_hash
                )
                """
            ),
            [
                {
                    "id": 1,
                    "feed": "github",
                    "summary": "Critical GitHub advisory",
                    "severity": "critical",
                    "ecosystem": "github",
                    "package": "example/action",
                    "affected_ranges": "<2.0.0",
                    "aliases": "GHSA-test-1",
                    "source_url": "https://example.test/advisories/1",
                    "published_at": now - timedelta(days=1),
                    "fetched_at": now,
                    "identities": "GHSA-test-1",
                    "content_hash": "hash-1",
                },
                {
                    "id": 2,
                    "feed": "npm",
                    "summary": "High npm advisory",
                    "severity": "high",
                    "ecosystem": "npm",
                    "package": "example-package",
                    "affected_ranges": "<1.5.0",
                    "aliases": "npm-test-2",
                    "source_url": "https://example.test/advisories/2",
                    "published_at": now - timedelta(days=2),
                    "fetched_at": now,
                    "identities": "npm-test-2",
                    "content_hash": "hash-2",
                },
                {
                    "id": 3,
                    "feed": "pypi",
                    "summary": "Low PyPI advisory",
                    "severity": "low",
                    "ecosystem": "pypi",
                    "package": "example-project",
                    "affected_ranges": "<0.2.0",
                    "aliases": "pypi-test-3",
                    "source_url": "https://example.test/advisories/3",
                    "published_at": now - timedelta(days=3),
                    "fetched_at": now,
                    "identities": "pypi-test-3",
                    "content_hash": "hash-3",
                },
            ],
        )
        session.execute(
            text(
                """
                INSERT INTO vuln_links (
                    id, advisory_id, server_id, match_basis, match_value,
                    match_confidence, linked_at
                ) VALUES (
                    :id, :advisory_id, :server_id, :match_basis, :match_value,
                    :match_confidence, :linked_at
                )
                """
            ),
            [
                {
                    "id": 1,
                    "advisory_id": 1,
                    "server_id": "server-100",
                    "match_basis": "package",
                    "match_value": "example/action",
                    "match_confidence": 0.99,
                    "linked_at": now,
                },
                {
                    "id": 2,
                    "advisory_id": 1,
                    "server_id": "server-101",
                    "match_basis": "package",
                    "match_value": "example/action",
                    "match_confidence": 0.95,
                    "linked_at": now,
                },
                {
                    "id": 3,
                    "advisory_id": 2,
                    "server_id": "server-200",
                    "match_basis": "package",
                    "match_value": "example-package",
                    "match_confidence": 0.90,
                    "linked_at": now,
                },
                {
                    "id": 4,
                    "advisory_id": 3,
                    "server_id": "server-300",
                    "match_basis": "package",
                    "match_value": "example-project",
                    "match_confidence": 0.85,
                    "linked_at": now,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/vuln/advisories/rollup",
            params={"ecosystem": "", "severity": "", "days": 30},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["by_severity"]["critical"] == 1, payload
        assert payload["top_linked"][0]["server_count"] == 2, payload
        assert len(payload["by_ecosystem"]) == 3, payload
        assert payload["window_days"] == 30, payload

    print("PASS")


if __name__ == "__main__":
    run()