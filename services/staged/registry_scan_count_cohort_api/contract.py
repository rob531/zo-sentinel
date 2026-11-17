from collections.abc import Generator

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


app = FastAPI()


@app.get("/api/registry/scan-count-cohorts")
def get_scan_count_cohorts(session: Session = Depends(get_session)) -> dict:
    cohort_rows = session.execute(
        text(
            """
            SELECT
                CASE
                    WHEN scan_count = 0 THEN 'never'
                    WHEN scan_count = 1 THEN 'once'
                    WHEN scan_count <= 3 THEN 'few'
                    WHEN scan_count <= 10 THEN 'many'
                    ELSE 'heavy'
                END AS cohort,
                COUNT(*) AS servers,
                COUNT(DISTINCT registry_source) AS sources
            FROM mcp_server_registry
            GROUP BY cohort
            """
        )
    ).mappings().all()

    source_rows = session.execute(
        text(
            """
            SELECT
                registry_source,
                CASE
                    WHEN scan_count = 0 THEN 'never'
                    WHEN scan_count = 1 THEN 'once'
                    WHEN scan_count <= 3 THEN 'few'
                    WHEN scan_count <= 10 THEN 'many'
                    ELSE 'heavy'
                END AS cohort,
                COUNT(*) AS servers
            FROM mcp_server_registry
            GROUP BY registry_source, cohort
            """
        )
    ).mappings().all()

    total_servers = session.execute(
        text("SELECT COUNT(*) FROM mcp_server_registry")
    ).scalar_one()

    return {
        "total_servers": int(total_servers),
        "cohorts": [
            {
                "cohort": row["cohort"],
                "servers": int(row["servers"]),
                "sources": int(row["sources"]),
            }
            for row in cohort_rows
        ],
        "by_source": [
            {
                "registry_source": row["registry_source"],
                "cohort": row["cohort"],
                "servers": int(row["servers"]),
            }
            for row in source_rows
        ],
    }


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-scan-count-0",
                    name="Never scanned",
                    registry_source="source-a",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="registry-scan-count-1",
                    name="Scanned once",
                    registry_source="source-a",
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="registry-scan-count-2",
                    name="Scanned twice",
                    registry_source="source-b",
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="registry-scan-count-7",
                    name="Scanned seven times",
                    registry_source="source-b",
                    scan_count=7,
                ),
                McpServerRegistry(
                    server_id="registry-scan-count-40",
                    name="Scanned forty times",
                    registry_source="source-a",
                    scan_count=40,
                ),
            ]
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/registry/scan-count-cohorts")
        assert response.status_code == 200
        payload = response.json()

        assert payload["total_servers"] == 5

        cohorts = {row["cohort"]: row for row in payload["cohorts"]}
        assert cohorts["never"]["servers"] == 1
        assert cohorts["heavy"]["servers"] == 1
        assert len(payload["by_source"]) >= 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()