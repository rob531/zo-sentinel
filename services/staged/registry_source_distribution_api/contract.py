from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.registry_source_distribution_api.router import router


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    rows = [
        {
            "server_id": "contract-server-1",
            "name": "Contract Server 1",
            "registry_source": "alpha",
            "trust_score": 0.8,
            "verdict": "trusted",
            "risk_tier": "low",
            "first_seen": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "last_seen": datetime(2025, 1, 4, tzinfo=timezone.utc),
            "last_scanned": datetime(2025, 1, 4, tzinfo=timezone.utc),
            "scan_count": 2,
        },
        {
            "server_id": "contract-server-2",
            "name": "Contract Server 2",
            "registry_source": "alpha",
            "trust_score": 0.6,
            "verdict": "review",
            "risk_tier": "medium",
            "first_seen": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "last_seen": datetime(2025, 1, 3, tzinfo=timezone.utc),
            "last_scanned": datetime(2025, 1, 3, tzinfo=timezone.utc),
            "scan_count": 3,
        },
        {
            "server_id": "contract-server-3",
            "name": "Contract Server 3",
            "registry_source": "beta",
            "trust_score": 0.4,
            "verdict": "review",
            "risk_tier": "high",
            "first_seen": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "last_seen": datetime(2025, 1, 5, tzinfo=timezone.utc),
            "last_scanned": datetime(2025, 1, 5, tzinfo=timezone.utc),
            "scan_count": 4,
        },
        {
            "server_id": "contract-server-4",
            "name": "Contract Server 4",
            "registry_source": "beta",
            "trust_score": 0.2,
            "verdict": "untrusted",
            "risk_tier": "critical",
            "first_seen": datetime(2025, 1, 3, tzinfo=timezone.utc),
            "last_seen": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "last_scanned": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "scan_count": 1,
        },
    ]
    test_session.add_all(McpServerRegistry(**row) for row in rows)
    test_session.commit()

    def override_get_session():
        session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
        try:
            yield session
        finally:
            session.close()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    expected = {
        "alpha": {
            "server_count": 2,
            "avg_trust_score": 0.7,
            "tiers": {"low", "medium"},
        },
        "beta": {
            "server_count": 2,
            "avg_trust_score": 0.3,
            "tiers": {"high", "critical"},
        },
    }

    with TestClient(local_app) as client:
        response = client.get("/api/registry/sources")
        assert response.status_code == 200, response.text
        payload = response.json()
        sources = payload["sources"]
        assert len(sources) == 2
        assert payload["total_servers"] == 4
        assert payload["source_count"] == 2
        assert sum(source["server_count"] for source in sources) == 4

        by_source = {source["registry_source"]: source for source in sources}
        assert set(by_source) == set(expected)
        for registry_source, expectations in expected.items():
            source = by_source[registry_source]
            assert source["server_count"] == expectations["server_count"]
            assert float(source["avg_trust_score"]) == expectations["avg_trust_score"]
            assert expectations["tiers"].issubset(source["tier_counts"])

    test_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run()