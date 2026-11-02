from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.registry_source_scan_backlog.router import router as service_router


def _build_test_app() -> tuple[FastAPI, sessionmaker[Session]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.utcnow()
    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-contract-1",
                    name="Contract Server One",
                    registry_source="github",
                    trust_score=0.91,
                    verdict="trusted",
                    confidence=0.95,
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_assessed=now,
                    last_scanned=None,
                    scan_count=0,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="registry-contract-2",
                    name="Contract Server Two",
                    registry_source="github",
                    trust_score=0.72,
                    verdict=None,
                    confidence=0.80,
                    first_seen=now - timedelta(days=20),
                    last_seen=now,
                    last_assessed=now,
                    last_scanned=now - timedelta(days=1),
                    scan_count=4,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="registry-contract-3",
                    name="Contract Server Three",
                    registry_source="npm",
                    trust_score=0.44,
                    verdict=None,
                    confidence=0.60,
                    first_seen=now - timedelta(days=40),
                    last_seen=now - timedelta(days=2),
                    last_assessed=now - timedelta(days=10),
                    last_scanned=now - timedelta(days=14),
                    scan_count=9,
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

    application = FastAPI()
    route_paths = [getattr(route, "path", "") for route in service_router.routes]
    prefix = "" if any(path == "/api" or path.startswith("/api/") for path in route_paths) else "/api"
    application.include_router(service_router, prefix=prefix)

    def override_get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application, session_factory


def main() -> None:
    application, _ = _build_test_app()

    with TestClient(application) as client:
        response = client.get("/api/registry/source-scan-backlog")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_sources"] == 2, payload
        assert payload["total_servers"] == 3, payload

        sources = payload["sources"]
        assert len(sources) == 2, payload
        assert sum(source["never_scanned"] for source in sources) >= 1, payload

        for source in sources:
            coverage = source["verdict_coverage_pct"]
            assert isinstance(coverage, int) and not isinstance(coverage, bool), payload
            assert 0 <= coverage <= 100, payload

        assert sources == sorted(
            sources,
            key=lambda source: (-source["server_count"], source["registry_source"]),
        ), payload

    print("PASS")


if __name__ == "__main__":
    main()