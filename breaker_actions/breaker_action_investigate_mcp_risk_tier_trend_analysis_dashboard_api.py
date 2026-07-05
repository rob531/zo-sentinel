from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores, McpScoreDisputes, Orgs, Users
import requests

router = APIRouter()

@router.get("/investigate/mcp_risk_tier_trend_analysis_dashboard_api")
async def investigate_mcp_risk_tier_trend_analysis_dashboard_api(session: Session = Depends(get_session)):
    try:
        # Query app tables
        app_tables_data = {
            "mcp_server_registry": session.query(McpServerRegistry).all(),
            "mcp_llm_axis_scores": session.query(McpLlmAxisScores).all(),
            "mcp_score_disputes": session.query(McpScoreDisputes).all(),
            "orgs": session.query(Orgs).all(),
            "users": session.query(Users).all()
        }

        # Query mesh/pipeline tables
        mesh_tables_data = {
            "mcp_signal_scores": requests.post("http://127.0.0.1:8772/query", json={"table": "mcp_signal_scores"}).json(),
            "mesh_memory": requests.post("http://127.0.0.1:8772/query", json={"table": "mesh_memory"}).json()
        }

        return {"app_tables": app_tables_data, "mesh_tables": mesh_tables_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import sqlite3
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create an in-memory SQLite database for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency
    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi.testclient import TestClient
    from app.main import app

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/investigate/mcp_risk_tier_trend_analysis_dashboard_api")
    if response.status_code == 200:
        print("PASS")
    else:
        print("FAIL")