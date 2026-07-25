from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from app.routers.verdict_breakdown_api import router as verdict_router
from app.router_registry import register_router

def wire_verdict_breakdown():
    app = FastAPI()

    # Register the verdict breakdown router
    register_router(app, verdict_router, prefix="/verdict")

    return app

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override the dependency for testing
    app = wire_verdict_breakdown()
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/verdict/1")
    assert response.status_code == 404, "Test server ID should not exist"

    print("PASS")