from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

VULN_ADVISORIES_TABLE = VulnAdvisory.__tablename__
VULN_LINKS_TABLE = VulnLink.__tablename__

router = APIRouter(prefix="/api")


@router.get("/vulns/package-hotspots")
def get_package_hotspots(
    limit: int = Query(default=10, ge=1),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    statement = text(
        f"""
        SELECT
            a.package AS package,
            a.ecosystem AS ecosystem,
            COUNT(DISTINCT a.id) AS advisory_count,
            COUNT(DISTINCT l.server_id) AS linked_server_count,
            CASE MAX(
                CASE UPPER(a.severity)
                    WHEN 'CRITICAL' THEN 4
                    WHEN 'HIGH' THEN 3
                    WHEN 'MEDIUM' THEN 2
                    WHEN 'LOW' THEN 1
                    ELSE 0
                END
            )
                WHEN 4 THEN 'CRITICAL'
                WHEN 3 THEN 'HIGH'
                WHEN 2 THEN 'MEDIUM'
                WHEN 1 THEN 'LOW'
                ELSE NULL
            END AS max_severity,
            MAX(a.published_at) AS latest_published_at
        FROM {VULN_ADVISORIES_TABLE} AS a
        INNER JOIN {VULN_LINKS_TABLE} AS l
            ON l.advisory_id = a.id
        GROUP BY a.package, a.ecosystem
        ORDER BY
            linked_server_count DESC,
            advisory_count DESC,
            a.package ASC,
            a.ecosystem ASC
        LIMIT :limit
        """
    )
    rows = session.execute(statement, {"limit": limit}).mappings().all()
    hotspots: List[Dict[str, Any]] = [dict(row) for row in rows]
    return {"hotspots": hotspots, "limit": limit}


app = FastAPI(title="Advisory Package Hotspot API")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    package TEXT NOT NULL,
                    ecosystem TEXT NOT NULL,
                    severity TEXT,
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
                    advisory_id INTEGER NOT NULL,
                    server_id TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, package, ecosystem, severity, published_at)
                VALUES
                    (1, 'requests', 'pypi', 'HIGH', '2025-01-10T00:00:00Z'),
                    (2, 'lodash', 'npm', 'CRITICAL', '2025-01-11T00:00:00Z')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links (id, advisory_id, server_id)
                VALUES
                    (1, 1, 'mcp-server-a'),
                    (2, 1, 'mcp-server-b'),
                    (3, 2, 'mcp-server-c')
                """
            )
        )

    def override_get_session():
        testing_session = TestingSession()
        try:
            yield testing_session
        finally:
            testing_session.close()

    that_app = FastAPI(title="Advisory Package Hotspot API Test")
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vulns/package-hotspots", params={"limit": 10})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["limit"] == 10
        assert payload["hotspots"][0]["package"] == "requests"
        assert payload["hotspots"][0]["linked_server_count"] == 2
        assert payload["hotspots"][0]["max_severity"] == "HIGH"

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()