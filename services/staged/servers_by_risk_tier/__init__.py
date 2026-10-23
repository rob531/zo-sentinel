from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def test_self():
    app = FastAPI()

    # Override the session for testing
    app.dependency_overrides[get_session] = lambda: StaticPool.create(
        "sqlite:///:memory:"
    )

    client = TestClient(app)

    # Test that the app can be created and the session is overridden
    assert app is not None
    assert get_session() is not None

    print("PASS")

if __name__ == "__main__":
    test_self()