from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def get_link_provenance(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(
            VulnLink.match_basis.label("match_basis"),
            func.count(VulnLink.id).label("links"),
            func.count(VulnLink.server_id.distinct()).label("servers"),
            func.count(VulnLink.advisory_id.distinct()).label("advisories"),
            func.avg(VulnLink.match_confidence).label("avg_confidence"),
            func.max(VulnLink.match_confidence).label("max_confidence"),
            func.min(VulnLink.linked_at).label("first_linked_at"),
            func.max(VulnLink.linked_at).label("last_linked_at"),
        )
        .select_from(VulnLink)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .group_by(VulnLink.match_basis)
        .order_by(VulnLink.match_basis)
    ).all()

    bases = [
        {
            "match_basis": row.match_basis,
            "links": int(row.links or 0),
            "servers": int(row.servers or 0),
            "advisories": int(row.advisories or 0),
            "avg_confidence": float(row.avg_confidence or 0.0),
            "max_confidence": float(row.max_confidence or 0.0),
            "first_linked_at": row.first_linked_at,
            "last_linked_at": row.last_linked_at,
        }
        for row in rows
    ]

    return {
        "bases": bases,
        "total_links": sum(base["links"] for base in bases),
    }


def get_vuln_link_provenance(session: Session) -> dict[str, Any]:
    return get_link_provenance(session)


def get_vuln_link_provenance_audit(session: Session) -> dict[str, Any]:
    return get_link_provenance(session)


def get_provenance_audit(session: Session) -> dict[str, Any]:
    return get_link_provenance(session)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_advisories (
                id INTEGER PRIMARY KEY,
                severity TEXT,
                ecosystem TEXT
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE vuln_links (
                id INTEGER PRIMARY KEY,
                advisory_id INTEGER,
                server_id INTEGER,
                match_basis TEXT,
                match_value TEXT,
                match_confidence FLOAT,
                linked_at DATETIME
            )
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO vuln_advisories (id, severity, ecosystem)
            VALUES (1, 'HIGH', 'npm'), (2, 'MEDIUM', 'pypi')
            """
        )
        connection.exec_driver_sql(
            """
            INSERT INTO vuln_links
                (id, advisory_id, server_id, match_basis, match_value,
                 match_confidence, linked_at)
            VALUES
                (1, 1, 10, 'package', 'example', 0.9, '2024-01-01 00:00:00'),
                (2, 1, 11, 'package', 'example', 0.7, '2024-01-02 00:00:00'),
                (3, 2, 12, 'cpe', 'cpe:/a:example', 0.4,
                 '2024-01-03 00:00:00')
            """
        )

    that_app = FastAPI()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/vuln/links/provenance")
    def provenance_endpoint(session: Session = Depends(get_session)):
        return get_link_provenance(session)

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/links/provenance")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["bases"]) == 2
        assert payload["total_links"] == 3
        package_basis = next(
            base for base in payload["bases"] if base["match_basis"] == "package"
        )
        assert abs(package_basis["avg_confidence"] - 0.8) < 1e-9

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()