from datetime import datetime, timedelta
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.models import FreshnessStat, McpServerRegistry
from services.staged.freshness_slo_status.router import router


def run_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    Base.metadata.create_all(
        engine,
        tables=[FreshnessStat.__table__, McpServerRegistry.__table__],
    )

    now = datetime.utcnow()
    fresh_surface = "contract-fresh"
    stale_surface = "contract-stale"
    missing_surface = "contract-missing"

    db = TestingSessionLocal()
    try:
        db.add_all(
            [
                FreshnessStat(
                    surface=fresh_surface,
                    payload={"source": "contract"},
                    computed_at=now - timedelta(minutes=5),
                    compute_seconds=0.25,
                ),
                FreshnessStat(
                    surface=stale_surface,
                    payload={"source": "contract"},
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.5,
                ),
                McpServerRegistry(
                    server_id="contract-fresh",
                    name=fresh_surface,
                    last_assessed=now,
                    first_seen=now - timedelta(days=1),
                ),
                McpServerRegistry(
                    server_id="contract-stale",
                    name=stale_surface,
                    last_assessed=now - timedelta(hours=2),
                    first_seen=now - timedelta(days=1),
                ),
                McpServerRegistry(
                    server_id="contract-missing",
                    name=missing_surface,
                    last_assessed=now - timedelta(days=3),
                    first_seen=now - timedelta(days=3),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/freshness/slo")
            assert response.status_code == 200, response.text

            body = response.json()
            assert isinstance(body.get("generated_at"), str)
            surfaces = body.get("surfaces")
            assert isinstance(surfaces, list)

            statuses = {item["status"] for item in surfaces}
            assert {"ok", "stale", "unknown"}.issubset(statuses), statuses

            severity = {"expired": 0, "stale": 1, "unknown": 2, "ok": 3}
            actual_order = [
                (severity[item["status"]], item["surface"])
                for item in surfaces
            ]
            assert actual_order == sorted(actual_order), actual_order

            filtered = client.get(
                "/api/freshness/slo",
                params={"surface": fresh_surface},
            )
            assert filtered.status_code == 200, filtered.text
            filtered_surfaces = filtered.json()["surfaces"]
            assert len(filtered_surfaces) == 1, filtered_surfaces
            assert filtered_surfaces[0]["surface"] == fresh_surface
            assert filtered_surfaces[0]["status"] == "ok"
    finally:
        engine.dispose()


if __name__ == "__main__":
    run_contract()
    print("PASS")