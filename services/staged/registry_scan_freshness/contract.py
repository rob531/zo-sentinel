from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.registry_scan_freshness.router import router


def _create_schema(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR NOT NULL PRIMARY KEY,
                    name VARCHAR,
                    registry_source VARCHAR,
                    last_scanned DATETIME,
                    last_seen DATETIME,
                    last_assessed DATETIME,
                    scan_count INTEGER DEFAULT 0,
                    risk_tier VARCHAR,
                    verdict VARCHAR,
                    confidence FLOAT,
                    description TEXT,
                    first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
                    meta TEXT,
                    trust_score FLOAT,
                    url VARCHAR,
                    verdict_reasoning TEXT
                )
                """
            )
        )


def _seed(session, now: datetime) -> None:
    session.execute(
        text(
            """
            INSERT INTO mcp_server_registry
                (server_id, name, registry_source, last_scanned, scan_count, risk_tier, verdict)
            VALUES
                (:server_id, :name, :registry_source, :last_scanned, :scan_count, :risk_tier, :verdict)
            """
        ),
        [
            {
                "server_id": "never-scanned",
                "name": "Never scanned",
                "registry_source": "seed-source-a",
                "last_scanned": None,
                "scan_count": 0,
                "risk_tier": "unknown",
                "verdict": "unknown",
            },
            {
                "server_id": "recently-scanned",
                "name": "Recently scanned",
                "registry_source": "seed-source-b",
                "last_scanned": now - timedelta(hours=2),
                "scan_count": 3,
                "risk_tier": "low",
                "verdict": "approved",
            },
            {
                "server_id": "oldly-scanned",
                "name": "Oldly scanned",
                "registry_source": "seed-source-a",
                "last_scanned": now - timedelta(days=40),
                "scan_count": 1,
                "risk_tier": "high",
                "verdict": "blocked",
            },
        ],
    )
    session.commit()


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _create_schema(engine)

    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    session = TestingSessionLocal()
    try:
        _seed(session, datetime.utcnow())
    finally:
        session.close()

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/registry/scan-freshness")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["buckets"]["never_scanned"] == 1
        assert payload["buckets"]["over_30d"] == 1
        assert sum(item["total"] for item in payload["by_source"]) == 3

    print("PASS")


if __name__ == "__main__":
    main()