from typing import List, Optional
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpLlmAxisScore, McpServerRegistry
from pydantic import BaseModel
import requests

class McpScoreDisputeRead(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    comment: Optional[str]
    resolved: bool
    created_at: str
    updated_at: str

class McpLlmAxisScoreRead(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    created_at: str
    updated_at: str

class McpServerRegistryRead(BaseModel):
    id: int
    server_name: str
    created_at: str
    updated_at: str

def get_score_disputes(db: Session = Depends(get_session)) -> List[McpScoreDisputeRead]:
    disputes = db.query(McpScoreDispute).all()
    return [McpScoreDisputeRead.from_orm(dispute) for dispute in disputes]

def get_llm_axis_scores(db: Session = Depends(get_session)) -> List[McpLlmAxisScoreRead]:
    scores = db.query(McpLlmAxisScore).all()
    return [McpLlmAxisScoreRead.from_orm(score) for score in scores]

def get_server_registry(db: Session = Depends(get_session)) -> List[McpServerRegistryRead]:
    servers = db.query(McpServerRegistry).all()
    return [McpServerRegistryRead.from_orm(server) for server in servers]

def get_signal_scores() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={
        "query": "SELECT * FROM mcp_signal_scores"
    })
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    return response.json()

def test_endpoint():
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import StaticPool
        from app.models import Base

        test_app = FastAPI()
        test_app.dependency_overrides[get_session] = lambda: Session(
            bind=Base.metadata.create_all(bind=Base.metadata.bind).bind,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            poolclass=StaticPool
        )

        client = TestClient(test_app)

        # Test get_score_disputes
        response = client.get("/score-disputes")
        assert response.status_code == 200

        # Test get_llm_axis_scores
        response = client.get("/llm-axis-scores")
        assert response.status_code == 200

        # Test get_server_registry
        response = client.get("/server-registry")
        assert response.status_code == 200

        # Test get_signal_scores
        response = client.get("/signal-scores")
        assert response.status_code == 200

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    test_endpoint()