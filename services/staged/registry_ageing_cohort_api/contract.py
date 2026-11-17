from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.registry_ageing_cohort_api.router import router


def run_contract_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )

    try:
        McpServerRegistry.__table__.create(bind=engine)

        newest = datetime(2025, 1, 31, 12, 0, tzinfo=timezone.utc)
        rows = [
            McpServerRegistry(
                server_id="fresh-server",
                name="Fresh Server",
                first_seen=newest - timedelta(days=200),
                last_seen=newest - timedelta(days=1),
                last_scanned=newest - timedelta(days=1),
                scan_count=12,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="recent-server",
                name="Recent Server",
                first_seen=newest - timedelta(days=180),
                last_seen=newest - timedelta(days=15),
                last_scanned=newest - timedelta(days=15),
                scan_count=8,
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="stale-server",
                name="Stale Server",
                first_seen=newest - timedelta(days=220),
                last_seen=newest - timedelta(days=60),
                last_scanned=newest - timedelta(days=60),
                scan_count=4,
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="dormant-server",
                name="Dormant Server",
                first_seen=newest - timedelta(days=300),
                last_seen=newest - timedelta(days=120),
                last_scanned=newest - timedelta(days=120),
                scan_count=1,
                risk_tier="critical",
            ),
        ]

        with testing_session() as session:
            session.add_all(rows)
            session.commit()

        def override_get_session() -> Iterator[Session]:
            session = testing_session()
            try:
                yield session
            finally:
                session.close()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/registry/ageing-cohorts")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["days"] == 90
            assert payload["total_servers"] == 4
            assert payload["cutoff"]

            cohorts = payload["cohorts"]
            assert len(cohorts) == 4

            by_cohort = {entry["cohort"]: entry for entry in cohorts}
            assert set(by_cohort) == {"fresh", "recent", "stale", "dormant"}
            assert by_cohort["fresh"]["count"] == 1

            expected_servers = {
                "fresh": "fresh-server",
                "recent": "recent-server",
                "stale": "stale-server",
                "dormant": "dormant-server",
            }
            for cohort_name, expected_server_id in expected_servers.items():
                servers = by_cohort[cohort_name]["servers"]
                assert servers
                assert all(server.get("server_id") for server in servers)
                assert {server["server_id"] for server in servers} == {
                    expected_server_id
                }

            limited = client.get(
                "/api/registry/ageing-cohorts",
                params={"limit": 1},
            )
            assert limited.status_code == 200, limited.text
            assert all(
                len(entry["servers"]) <= 1 for entry in limited.json()["cohorts"]
            )
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_contract_test()