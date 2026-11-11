from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class MatchBasisSummary(BaseModel):
    match_basis: str
    count: int
    avg_confidence: float


class VulnerabilityLink(BaseModel):
    server_id: int
    advisory_id: int
    advisory_summary: Optional[str]
    severity: Optional[str]
    ecosystem: Optional[str]
    package: Optional[str]
    match_basis: str
    match_value: Optional[str]
    match_confidence: Optional[float]
    linked_at: Optional[datetime]
    source_url: Optional[str]


class VulnerabilityLinksResponse(BaseModel):
    total: int
    by_match_basis: List[MatchBasisSummary]
    by_severity: Dict[str, int]
    links: List[VulnerabilityLink]


router = APIRouter(prefix="/api")


@router.get("/vuln/links", response_model=VulnerabilityLinksResponse)
def list_vulnerability_links(
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session=Depends(get_session),
):
    statement = select(
        VulnLink.server_id,
        VulnLink.advisory_id,
        VulnAdvisory.summary.label("advisory_summary"),
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnLink.match_basis,
        VulnLink.match_value,
        VulnLink.match_confidence,
        VulnLink.linked_at,
        VulnAdvisory.source_url,
    ).join(
        VulnAdvisory,
        VulnAdvisory.id == VulnLink.advisory_id,
    )

    if severity is not None:
        statement = statement.where(
            func.lower(VulnAdvisory.severity) == func.lower(severity)
        )

    statement = statement.order_by(
        VulnLink.linked_at.desc(),
        VulnLink.id.desc(),
    ).limit(limit)

    rows = session.execute(statement).mappings().all()

    basis_counts = defaultdict(int)
    basis_confidences = defaultdict(list)
    severity_counts = defaultdict(int)
    links = []

    for row in rows:
        basis = row["match_basis"]
        confidence = row["match_confidence"]
        severity_value = row["severity"]

        basis_counts[basis] += 1
        if confidence is not None:
            basis_confidences[basis].append(float(confidence))

        severity_counts[severity_value or "unknown"] += 1
        links.append(
            VulnerabilityLink(
                server_id=row["server_id"],
                advisory_id=row["advisory_id"],
                advisory_summary=row["advisory_summary"],
                severity=severity_value,
                ecosystem=row["ecosystem"],
                package=row["package"],
                match_basis=basis,
                match_value=row["match_value"],
                match_confidence=(
                    float(confidence) if confidence is not None else None
                ),
                linked_at=row["linked_at"],
                source_url=row["source_url"],
            )
        )

    by_match_basis = [
        MatchBasisSummary(
            match_basis=basis,
            count=basis_counts[basis],
            avg_confidence=(
                sum(basis_confidences[basis]) / len(basis_confidences[basis])
                if basis_confidences[basis]
                else 0.0
            ),
        )
        for basis in sorted(basis_counts)
    ]

    return VulnerabilityLinksResponse(
        total=len(links),
        by_match_basis=by_match_basis,
        by_severity=dict(sorted(severity_counts.items())),
        links=links,
    )


app = FastAPI()
app.include_router(router)


def _create_test_store():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    summary TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    source_url TEXT,
                    published_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id INTEGER,
                    match_basis TEXT,
                    match_value TEXT,
                    match_confidence REAL,
                    linked_at TEXT
                )
                """
            )
        )

        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, summary, severity, ecosystem, package,
                     source_url, published_at)
                VALUES
                    (1, 'nvd', 'Critical flaw in package A', 'critical',
                     'npm', 'package-a', 'https://example.test/a',
                     '2024-01-01 00:00:00'),
                    (2, 'osv', 'High impact issue in package B', 'high',
                     'pypi', 'package-b', 'https://example.test/b',
                     '2024-01-02 00:00:00')
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
                    (1, 1, 101, 'package', 'package-a', 0.90,
                     '2024-01-03 12:00:00'),
                    (2, 1, 102, 'package', 'package-a', 0.80,
                     '2024-01-02 12:00:00'),
                    (3, 2, 103, 'cpe', 'cpe:example:package-b', 0.95,
                     '2024-01-01 12:00:00')
                """
            )
        )

    return engine, SessionLocal


def main():
    engine, SessionLocal = _create_test_store()
    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/vuln/links")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 3
            assert any(
                item["match_basis"] == "package" and item["count"] == 2
                for item in payload["by_match_basis"]
            )
            assert payload["links"][0]["advisory_summary"] is not None
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    main()