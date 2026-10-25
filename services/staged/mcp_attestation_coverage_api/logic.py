from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _coverage_pct(attested: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((attested / total) * 100, 2)


def get_attestation_coverage(db: Session) -> dict[str, Any]:
    registry = McpServerRegistry.__table__
    server_id = registry.c.server_id.name
    registry_source = registry.c.registry_source.name

    source_rows = db.execute(
        text(
            f"""
            SELECT
                COALESCE(r.{registry_source}, 'unknown') AS source,
                COUNT(DISTINCT r.{server_id}) AS total_servers,
                COUNT(DISTINCT a.server_id) AS attested_servers
            FROM {registry.name} AS r
            LEFT JOIN mcp_attestations AS a
                ON a.server_id = r.{server_id}
            GROUP BY COALESCE(r.{registry_source}, 'unknown')
            ORDER BY source
            """
        )
    ).mappings()

    sources: list[dict[str, Any]] = []
    for row in source_rows:
        total_servers = int(row["total_servers"] or 0)
        attested_servers = int(row["attested_servers"] or 0)
        sources.append(
            {
                "source": str(row["source"]),
                "total_servers": total_servers,
                "attested_servers": attested_servers,
                "coverage_pct": _coverage_pct(attested_servers, total_servers),
                "never_attested": max(total_servers - attested_servers, 0),
            }
        )

    fleet_row = db.execute(
        text(
            f"""
            SELECT
                COUNT(DISTINCT r.{server_id}) AS scanned,
                COUNT(DISTINCT a.server_id) AS attested
            FROM {registry.name} AS r
            LEFT JOIN mcp_attestations AS a
                ON a.server_id = r.{server_id}
            """
        )
    ).mappings().one()

    scanned = int(fleet_row["scanned"] or 0)
    attested = int(fleet_row["attested"] or 0)

    return {
        "sources": sources,
        "fleet": {
            "scanned": scanned,
            "attested": attested,
            "coverage_pct": _coverage_pct(attested, scanned),
        },
    }


def compute_attestation_coverage(db: Session) -> dict[str, Any]:
    return get_attestation_coverage(db)


def get_coverage(db: Session) -> dict[str, Any]:
    return get_attestation_coverage(db)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    registry_source TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_attestations (
                    server_id TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, registry_source)
                VALUES
                    ('server-1', 'source-a'),
                    ('server-2', 'source-a'),
                    ('server-3', 'source-b')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_attestations (server_id)
                VALUES
                    ('server-1'),
                    ('server-2')
                """
            )
        )

    testing_app = FastAPI()
    testing_session = TestingSession()

    @testing_app.get("/api/attestations/coverage")
    def attestation_coverage(db: Session = Depends(get_session)):
        return get_attestation_coverage(db)

    testing_app.dependency_overrides[get_session] = lambda: testing_session

    try:
        with TestClient(testing_app) as client:
            response = client.get("/api/attestations/coverage")
            assert response.status_code == 200
            payload = response.json()
            assert payload["fleet"] == {
                "scanned": 3,
                "attested": 2,
                "coverage_pct": 66.67,
            }
            by_source = {item["source"]: item for item in payload["sources"]}
            assert by_source["source-a"]["total_servers"] == 2
            assert by_source["source-a"]["attested_servers"] == 2
            assert by_source["source-a"]["coverage_pct"] == 100.0
            assert by_source["source-a"]["never_attested"] == 0
            assert by_source["source-b"]["total_servers"] == 1
            assert by_source["source-b"]["attested_servers"] == 0
            assert by_source["source-b"]["coverage_pct"] == 0.0
            assert by_source["source-b"]["never_attested"] == 1
    finally:
        testing_session.close()
        engine.dispose()

    print("PASS")