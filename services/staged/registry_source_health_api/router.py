from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_source_health

router = APIRouter(prefix="/api")


@router.get("/registry/source-health")
def registry_source_health(db: Session = Depends(get_session)):
    """Return health summary grouped by registry source."""
    return get_registry_source_health(db)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import datetime

    from app.db import Base, get_session
    from app.models import McpServerRegistry

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite database and override the session dependency
    # ------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with the router and the overridden dependency
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Seed the in‑memory DB with four servers across two registry sources
    # ------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="srv-1",
                    registry_source="source-A",
                    trust_score=85,
                    verdict="good",
                    risk_tier="low",
                    last_seen=datetime(2023, 1, 1, 0, 0, 0),
                ),
                McpServerRegistry(
                    server_id="srv-2",
                    registry_source="source-A",
                    trust_score=70,
                    verdict="bad",
                    risk_tier="high",
                    last_seen=datetime(2023, 1, 2, 0, 0, 0),
                ),
                McpServerRegistry(
                    server_id="srv-3",
                    registry_source="source-B",
                    trust_score=92,
                    verdict="good",
                    risk_tier="medium",
                    last_seen=datetime(2023, 1, 3, 0, 0, 0),
                ),
                McpServerRegistry(
                    server_id="srv-4",
                    registry_source="source-B",
                    trust_score=60,
                    verdict="unknown",
                    risk_tier="low",
                    last_seen=datetime(2023, 1, 4, 0, 0, 0),
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute a test request and validate the contract
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/registry/source-health")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert "sources" in payload, "Missing 'sources' key in response"
    assert len(payload["sources"]) == 2, "Expected two source rows"
    total_servers = sum(src.get("server_count", 0) for src in payload["sources"])
    assert total_servers == 4, f"Expected total server_count 4, got {total_servers}"
    print("PASS")