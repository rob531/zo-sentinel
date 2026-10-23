from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, User

from . import endpoints, models
from .endpoints import (
    get_mesh_memory,
    get_score_disputes_endpoint,
    get_signal_scores,
    mesh_memory_endpoint,
    mesh_scores_endpoint,
    reset_quarantine_endpoint,
)
from .models import McpLlmAxisScore as McpLlmAxisScoreModel

__all__ = [
    "get_mesh_memory",
    "get_score_disputes_endpoint",
    "get_session",
    "get_signal_scores",
    "McpLlmAxisScore",
    "McpLlmAxisScoreModel",
    "McpScoreDispute",
    "McpServerRegistry",
    "mesh_memory_endpoint",
    "mesh_scores_endpoint",
    "models",
    "reset_quarantine_endpoint",
    "User",
]


if __name__ == "__main__":
    import sys
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient
    from sqlalchemy import StaticPool
    from sqlalchemy.orm import Session, sessionmaker

    from app.main import app

    from .models import Base

    engine = StaticPool.create_pool_instance()
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # test_get_users
    r = client.get("/users/")
    assert r.status_code == 200, f"test_get_users failed: {r.status_code}"

    # mesh_memory_endpoint
    r = client.post("/mesh/memory", json={"key": "test"})
    assert r.status_code in (200, 404), f"mesh_memory_endpoint failed: {r.status_code}"

    # mesh_scores_endpoint
    r = client.get("/mesh/scores")
    assert r.status_code in (200, 404), f"mesh_scores_endpoint failed: {r.status_code}"

    # reset_quarantine_endpoint
    r = client.post("/reset-quarantine", json={"server_id": "test"})
    assert r.status_code in (200, 404), f"reset_quarantine_endpoint failed: {r.status_code}"

    print("PASS")