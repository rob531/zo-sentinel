from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api", tags=["advisory-package-hotspots"])


def _serialize_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def list_package_hotspots(
    session: Session = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    severity_rank = case(
        (func.upper(VulnAdvisory.severity) == "CRITICAL", 4),
        (func.upper(VulnAdvisory.severity) == "HIGH", 3),
        (func.upper(VulnAdvisory.severity) == "MEDIUM", 2),
        (func.upper(VulnAdvisory.severity) == "LOW", 1),
        else_=0,
    )

    grouped = (
        select(
            VulnAdvisory.package.label("package"),
            VulnAdvisory.ecosystem.label("ecosystem"),
            func.count(func.distinct(VulnAdvisory.id)).label("advisory_count"),
            func.count(func.distinct(VulnLink.server_id)).label("linked_server_count"),
            func.max(severity_rank).label("max_severity_rank"),
            func.max(VulnAdvisory.published_at).label("latest_published_at"),
        )
        .select_from(
            VulnAdvisory.__table__.join(
                VulnLink.__table__,
                VulnLink.__table__.c.advisory_id == VulnAdvisory.__table__.c.id,
            )
        )
        .group_by(VulnAdvisory.package, VulnAdvisory.ecosystem)
        .subquery()
    )

    max_severity = case(
        (grouped.c.max_severity_rank == 4, "CRITICAL"),
        (grouped.c.max_severity_rank == 3, "HIGH"),
        (grouped.c.max_severity_rank == 2, "MEDIUM"),
        (grouped.c.max_severity_rank == 1, "LOW"),
        else_="UNKNOWN",
    )

    statement = (
        select(
            grouped.c.package,
            grouped.c.ecosystem,
            grouped.c.advisory_count,
            grouped.c.linked_server_count,
            max_severity.label("max_severity"),
            grouped.c.latest_published_at,
        )
        .order_by(
            grouped.c.linked_server_count.desc(),
            grouped.c.advisory_count.desc(),
            grouped.c.max_severity_rank.desc(),
            grouped.c.latest_published_at.desc(),
            grouped.c.package.asc(),
            grouped.c.ecosystem.asc(),
        )
        .limit(limit)
    )

    hotspots: List[Dict[str, Any]] = []
    for row in session.execute(statement).mappings():
        hotspots.append(
            {
                "package": row["package"],
                "ecosystem": row["ecosystem"],
                "advisory_count": int(row["advisory_count"]),
                "linked_server_count": int(row["linked_server_count"]),
                "max_severity": row["max_severity"],
                "latest_published_at": _serialize_datetime(row["latest_published_at"]),
            }
        )

    return {"hotspots": hotspots, "limit": limit}


@router.get("/vulns/package-hotspots")
def package_hotspots(
    session: Session = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
) -> Dict[str, Any]:
    return list_package_hotspots(session=session, limit=limit)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
                package VARCHAR NOT NULL,
                ecosystem VARCHAR NOT NULL,
                severity VARCHAR NOT NULL,
                published_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                advisory_id INTEGER NOT NULL,
                server_id VARCHAR NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO vuln_advisories
                (id, package, ecosystem, severity, published_at)
            VALUES
                (1, 'busy-package', 'npm', 'HIGH', '2025-01-02 00:00:00'),
                (2, 'quiet-package', 'npm', 'MEDIUM', '2025-01-01 00:00:00')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO vuln_links (id, advisory_id, server_id)
            VALUES
                (1, 1, 'server-a'),
                (2, 1, 'server-b'),
                (3, 2, 'server-c')
            """
        )

    TestSession = sessionmaker(bind=test_engine)
    test_session = TestSession()

    def override_get_session():
        yield test_session

    from fastapi import FastAPI

    self_test_app = FastAPI()
    self_test_app.dependency_overrides[get_session] = override_get_session
    self_test_app.include_router(router)

    with TestClient(self_test_app) as client:
        response = client.get("/api/vulns/package-hotspots?limit=10")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["hotspots"][0]["package"] == "busy-package"
        assert payload["hotspots"][0]["linked_server_count"] == 2
        assert payload["hotspots"][0]["max_severity"] == "HIGH"

    test_session.close()
    test_engine.dispose()
    print("PASS")