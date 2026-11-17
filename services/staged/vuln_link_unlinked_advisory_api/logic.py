from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


class UnlinkedAdvisoryItem(BaseModel):
    advisory_id: str
    feed: str
    severity: str
    ecosystem: str
    package: str
    published_at: str


class UnlinkedAdvisoriesResponse(BaseModel):
    total_advisories: int
    linked: int
    unlinked: int
    advisories: list[UnlinkedAdvisoryItem]


def get_unlinked_advisories(
    session: Session,
    limit: int = 200,
) -> dict[str, Any]:
    """Return advisory counts and advisories that have no vuln_links rows."""
    bounded_limit = max(1, min(int(limit), 200))

    counts_result = session.execute(
        text(
            """
            WITH advisory_counts AS (
                SELECT
                    a.id AS advisory_id,
                    COUNT(l.id) AS link_count
                FROM vuln_advisories AS a
                LEFT JOIN vuln_links AS l
                    ON l.advisory_id = a.id
                GROUP BY a.id
            )
            SELECT
                COUNT(*) AS total_advisories,
                COALESCE(
                    SUM(CASE WHEN link_count > 0 THEN 1 ELSE 0 END),
                    0
                ) AS linked
            FROM advisory_counts
            """
        )
    )
    counts = counts_result.mappings().one()
    total_advisories = int(counts["total_advisories"] or 0)
    linked = int(counts["linked"] or 0)

    rows_result = session.execute(
        text(
            """
            WITH advisory_counts AS (
                SELECT
                    a.id AS advisory_id,
                    COUNT(l.id) AS link_count
                FROM vuln_advisories AS a
                LEFT JOIN vuln_links AS l
                    ON l.advisory_id = a.id
                GROUP BY a.id
            )
            SELECT
                a.id,
                a.feed,
                a.severity,
                a.ecosystem,
                a.package,
                a.published_at
            FROM advisory_counts AS c
            INNER JOIN vuln_advisories AS a
                ON a.id = c.advisory_id
            WHERE c.link_count = 0
            ORDER BY a.published_at DESC
            LIMIT :limit
            """
        ),
        {"limit": bounded_limit},
    )

    advisories = []
    for row in rows_result.mappings():
        published_at = row["published_at"]
        advisories.append(
            {
                "advisory_id": str(row["id"]),
                "feed": str(row["feed"] or ""),
                "severity": str(row["severity"] or ""),
                "ecosystem": str(row["ecosystem"] or ""),
                "package": str(row["package"] or ""),
                "published_at": "" if published_at is None else str(published_at),
            }
        )

    return {
        "total_advisories": total_advisories,
        "linked": linked,
        "unlinked": total_advisories - linked,
        "advisories": advisories,
    }


router = APIRouter(prefix="/api")


@router.get(
    "/vuln/unlinked-advisories",
    response_model=UnlinkedAdvisoriesResponse,
)
def get_unlinked_advisories_endpoint(
    limit: int = Query(200, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_unlinked_advisories(session, limit)


if __name__ == "__main__":
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
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

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
                    server_id TEXT,
                    match_confidence REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, summary, severity, ecosystem, package, published_at)
                VALUES
                    (1, 'feed-a', 'summary-a', 'high', 'npm', 'pkg-a', '2024-01-01'),
                    (2, 'feed-b', 'summary-b', 'medium', 'pypi', 'pkg-b', '2024-02-01'),
                    (3, 'feed-c', 'summary-c', 'low', 'go', 'pkg-c', '2024-03-01')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_confidence)
                VALUES
                    (1, 1, 'server-a', 0.99)
                """
            )
        )

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/unlinked-advisories")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_advisories"] == 3
        assert payload["linked"] == 1
        assert payload["unlinked"] == 2
        assert any(
            advisory["advisory_id"] == "2"
            for advisory in payload["advisories"]
        )

    print("PASS")