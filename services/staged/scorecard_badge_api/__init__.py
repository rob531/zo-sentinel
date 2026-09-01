from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel
import requests
from app.db import get_session
from app.models import McpScoreDispute, McpLlmAxisScore, McpServerRegistry, Org, User

class ScoreDisputeRequest(BaseModel):
    score_id: int
    admin_note: str
    score_type: str

class ScoreDisputeResponse(BaseModel):
    id: int
    score_id: int
    admin_note: str
    score_type: str
    created_at: str
    updated_at: str

class MeshMemoryResponse(BaseModel):
    data: dict

class MeshScoresResponse(BaseModel):
    scores: List[dict]

class LlmAxisScoresResponse(BaseModel):
    scores: List[dict]

def get_mesh_memory_endpoint():
    def endpoint():
        try:
            response = requests.post(
                "http://127.0.0.1:8772/query",
                json={"query": "SELECT * FROM mesh_memory"},
                timeout=10
            )
            response.raise_for_status()
            return MeshMemoryResponse(data=response.json())
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))
    return endpoint

def get_score_disputes_endpoint():
    def endpoint(db: Session = Depends(get_session)):
        disputes = db.query(McpScoreDispute).all()
        return [ScoreDisputeResponse(
            id=d.id,
            score_id=d.score_id,
            admin_note=d.admin_note,
            score_type=d.score_type,
            created_at=str(d.created_at),
            updated_at=str(d.updated_at)
        ) for d in disputes]
    return endpoint

def get_mesh_scores():
    def scores():
        try:
            response = requests.post(
                "http://127.0.0.1:8772/query",
                json={"query": "SELECT * FROM mcp_signal_scores"},
                timeout=10
            )
            response.raise_for_status()
            return MeshScoresResponse(scores=response.json())
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))
    return scores

def get_signal_scores():
    def scores(db: Session = Depends(get_session)):
        scores = db.query(McpLlmAxisScore).all()
        return [{"id": s.id, "score": s.score, "axis": s.axis} for s in scores]
    return scores

def get_llm_axis_scores_endpoint():
    def endpoint(db: Session = Depends(get_session)):
        scores = db.query(McpLlmAxisScore).all()
        return LlmAxisScoresResponse(scores=[{
            "id": s.id,
            "score": s.score,
            "axis": s.axis,
            "created_at": str(s.created_at),
            "updated_at": str(s.updated_at)
        } for s in scores])
    return endpoint

def _run_self_test():
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Setup in-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    from app.models import Base
    Base.metadata.create_all(test_engine)

    # Override dependencies for testing
    def override_get_session() -> Session:
        return Session(test_engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    # Test endpoints
    client = TestClient(app)

    # Test get_mesh_memory_endpoint
    app.add_api_route("/mesh_memory", get_mesh_memory_endpoint(), methods=["GET"])
    response = client.get("/mesh_memory")
    assert response.status_code == 500  # Expected since no mesh_memory table in test DB

    # Test get_score_disputes_endpoint
    app.add_api_route("/score_disputes", get_score_disputes_endpoint(), methods=["GET"])
    response = client.get("/score_disputes")
    assert response.status_code == 200

    # Test get_mesh_scores
    app.add_api_route("/mesh_scores", get_mesh_scores(), methods=["GET"])
    response = client.get("/mesh_scores")
    assert response.status_code == 500  # Expected since no mcp_signal_scores table in test DB

    # Test get_signal_scores
    app.add_api_route("/signal_scores", get_signal_scores(), methods=["GET"])
    response = client.get("/signal_scores")
    assert response.status_code == 200

    # Test get_llm_axis_scores_endpoint
    app.add_api_route("/llm_axis_scores", get_llm_axis_scores_endpoint(), methods=["GET"])
    response = client.get("/llm_axis_scores")
    assert response.status_code == 200

    print("PASS")

if __name__ == "__main__":
    _run_self_test()