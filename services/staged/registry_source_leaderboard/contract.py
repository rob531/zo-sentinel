from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.registry_source_leaderboard.router import router


def get_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    return session_factory()


def seed_registry(session):
    observed_at = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    session.execute(
        insert(McpServerRegistry),
        [
            {
                "server_id": "leaderboard-official-1",
                "name": "Official Server One",
                "registry_source": "official",
                "trust_score": 0.90,
                "verdict": "trusted",
                "confidence": 0.95,
                "risk_tier": "low",
                "last_seen": observed_at,
                "last_scanned": observed_at,
                "scan_count": 3,
            },
            {
                "server_id": "leaderboard-official-2",
                "name": "Official Server Two",
                "registry_source": "official",
                "trust_score": 0.80,
                "verdict": "trusted",
                "confidence": 0.85,
                "risk_tier": "medium",
                "last_seen": observed_at,
                "last_scanned": observed_at,
                "scan_count": 2,
            },
            {
                "server_id": "leaderboard-community-1",
                "name": "Community Server",
                "registry_source": "community",
                "trust_score": 0.60,
                "verdict": "review",
                "confidence": 0.70,
                "risk_tier": "medium",
                "last_seen": observed_at,
                "last_scanned": observed_at,
                "scan_count": 1,
            },
            {
                "server_id": "leaderboard-unscored-1",
                "name": "Unscored Server",
                "registry_source": "experimental",
                "trust_score": None,
                "verdict": "unknown",
                "confidence": 0.40,
                "risk_tier": "unknown",
                "last_seen": observed_at,
                "last_scanned": observed_at,
                "scan_count": 1,
            },
        ],
    )
    session.commit()


def run_contract():
    session = get_test_session()
    seed_registry(session)

    that_app = FastAPI()
    route_paths = [getattr(route, "path", "") for route in router.routes]
    prefix = "" if any(path.startswith("/api/") for path in route_paths) else "/api"
    that_app.include_router(router, prefix=prefix)

    def override_get_session():
        yield session

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get(
                "/api/registry/sources/leaderboard",
                params={"limit": 50},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            rows = payload["rows"]

            assert len(rows) == 3, rows
            assert payload["total_sources"] == 3, payload
            assert rows[0]["source"] == "official", rows
            assert rows[0]["server_count"] == 2, rows
            assert sum(row["server_count"] for row in rows) == 4, rows

            unscored = [row for row in rows if row["mean_trust_score"] is None]
            assert len(unscored) == 1, rows
            assert unscored[0]["source"] == "experimental", rows
    finally:
        session.close()

    print("PASS")


def run_self_test():
    run_contract()


def main():
    run_contract()


if __name__ == "__main__":
    main()