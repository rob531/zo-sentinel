from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def test_self():
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: get_session(StaticPool())

    client = TestClient(app)

    # Test that the app can be instantiated and dependencies can be overridden
    assert client.get("/").status_code == 404  # No routes defined, so 404 is expected

    print("PASS")

if __name__ == "__main__":
    test_self()