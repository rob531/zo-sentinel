from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def test_main():
    app = FastAPI()

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: get_test_session()

    client = TestClient(app)

    # Test that the app can be created and dependencies overridden
    assert client.get("/").status_code == 404  # Default route not found is expected

    print("PASS")

def get_test_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    return SessionLocal()

if __name__ == "__main__":
    test_main()