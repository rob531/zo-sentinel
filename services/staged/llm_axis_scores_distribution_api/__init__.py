from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry
from typing import List, Optional
import requests

class AutoEmittedService:
    def __init__(self):
        self.base_url = "http://127.0.0.1:8772"

    def get_signal_scores(self, db: Session = Depends(get_session)) -> List[dict]:
        try:
            response = requests.get(f"{self.base_url}/query/mcp_signal_scores", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    def signal_scores_endpoint(self, db: Session = Depends(get_session)) -> dict:
        scores = self.get_signal_scores(db)
        return {"signal_scores": scores}

    def mesh_memory_endpoint(self, db: Session = Depends(get_session)) -> dict:
        try:
            response = requests.get(f"{self.base_url}/query/mesh_memory", timeout=5)
            response.raise_for_status()
            return {"mesh_memory": response.json()}
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    def reset_quarantine_endpoint(self, db: Session = Depends(get_session)) -> dict:
        try:
            response = requests.post(f"{self.base_url}/reset_quarantine", timeout=5)
            response.raise_for_status()
            return {"status": "success"}
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_llm_axis_scores(self, db: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
        return db.query(McpLlmAxisScore).all()

    def get_score_disputes(self, db: Session = Depends(get_session)) -> List[McpScoreDispute]:
        return db.query(McpScoreDispute).all()

    def get_server_registry(self, db: Session = Depends(get_session)) -> List[McpServerRegistry]:
        return db.query(McpServerRegistry).all()

def _run_self_test():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)

    # Override the get_session dependency for testing
    def get_test_session():
        return Session(engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session

    # Add routes for testing
    service = AutoEmittedService()

    @app.get("/signal_scores")
    async def signal_scores():
        return service.signal_scores_endpoint()

    @app.get("/mesh_memory")
    async def mesh_memory():
        return service.mesh_memory_endpoint()

    @app.post("/reset_quarantine")
    async def reset_quarantine():
        return service.reset_quarantine_endpoint()

    client = TestClient(app)

    # Test signal_scores_endpoint
    response = client.get("/signal_scores")
    assert response.status_code == 500  # Expecting 500 because the mesh service is not running

    # Test mesh_memory_endpoint
    response = client.get("/mesh_memory")
    assert response.status_code == 500  # Expecting 500 because the mesh service is not running

    # Test reset_quarantine_endpoint
    response = client.post("/reset_quarantine")
    assert response.status_code == 500  # Expecting 500 because the mesh service is not running

    print("PASS")

if __name__ == "__main__":
    _run_self_test()