from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpScoreDisputes, Org, User
from .mcp_server_axis_probabilities_dashboard_view import axis_probabilities_dashboard_view

router = APIRouter()

@router.get("/dashboard/axis-probabilities", response_model=dict)
async def get_axis_probabilities_dashboard(
    session=Depends(get_session)
):
    return await axis_probabilities_dashboard_view(session)

def include_in_app(app):
    app.include_router(router, prefix="/dashboard", tags=["Dashboard"])
    app.add_api_route("/dashboard/axis-probabilities", get_axis_probabilities_dashboard, methods=["GET"])

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    app = FastAPI()
    include_in_app(app)

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(app)
    response = client.get("/dashboard/axis-probabilities")
    assert response.status_code == 200
    print("PASS")