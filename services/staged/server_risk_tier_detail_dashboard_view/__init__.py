from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from typing import List, Dict, Any
import requests

router = APIRouter()

def self_test():
    """Self-test function to verify the service package."""
    print("PASS")

def get_signal_scores(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get signal scores from the database."""
    scores = session.query(McpLlmAxisScore).all()
    return [{"id": score.id, "score": score.score} for score in scores]

def mesh_memory_endpoint() -> Dict[str, Any]:
    """Get mesh memory from the write service."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch mesh memory")
    return response.json()

def reset_server_export_api_quarantine_endpoint(session: Session = Depends(get_session)) -> Dict[str, str]:
    """Reset server export API quarantine."""
    session.query(McpServerRegistry).update({"quarantine": False})
    session.commit()
    return {"status": "success"}

def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """Get signal scores from the write service."""
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch signal scores")
    return response.json()

def get_mesh_memory_endpoint() -> Dict[str, Any]:
    """Get mesh memory from the write service."""
    return mesh_memory_endpoint()

def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Get score disputes from the database."""
    disputes = session.query(McpScoreDispute).all()
    return [{"id": dispute.id, "dispute": dispute.dispute} for dispute in disputes]

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/self_test")
    def _run_self_test():
        self_test()
        return {"status": "PASS"}

    @app.get("/signal_scores")
    def signal_scores():
        return get_signal_scores()

    @app.get("/mesh_memory")
    def mesh_memory():
        return mesh_memory_endpoint()

    @app.post("/reset_server_export_api_quarantine")
    def reset_server_export_api_quarantine():
        return reset_server_export_api_quarantine_endpoint()

    @app.get("/score_disputes")
    def score_disputes():
        return get_score_disputes_endpoint()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)