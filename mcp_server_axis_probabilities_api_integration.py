from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Organizations, Users
from .mcp_server_axis_probabilities_api import router as axis_probabilities_router

router = APIRouter()

router.include_router(
    axis_probabilities_router,
    prefix="/api/servers",
    tags=["servers"]
)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import get_session, Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    # Override the database dependency for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test if the route is present
    response = client.get("/api/servers/1/axis-probabilities")
    assert response.status_code != 404
    print("PASS")