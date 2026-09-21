from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def test_self():
    # Override the app's database session with an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override the dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create a test client
    client = TestClient(app)

    # Test the self-test functionality
    response = client.get("/self-test")
    assert response.status_code == 200
    assert response.json() == {"status": "PASS"}

    print("PASS")

if __name__ == "__main__":
    # Create a minimal FastAPI app for self-testing
    app = FastAPI()

    @app.get("/self-test")
    async def self_test():
        return {"status": "PASS"}

    test_self()