from fastapi import FastAPI, Depends, HTTPException
from typing import List, Optional
from pydantic import BaseModel
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.orm import Session
import requests
from datetime import datetime

app = FastAPI()

class MeshMemoryResponse(BaseModel):
    id: int
    mesh_id: int
    created_at: datetime
    updated_at: datetime
    data: dict

class SignalScoresResponse(BaseModel):
    id: int
    mesh_id: int
    signal_type: str
    score: float
    created_at: datetime

class ScoreDisputesResponse(BaseModel):
    id: int
    mesh_id: int
    dispute_type: str
    status: str
    created_at: datetime

def get_mesh_memory_endpoint(mesh_id: int, session: Session = Depends(get_session)) -> MeshMemoryResponse:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE mesh_id = {mesh_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    data = response.json()
    if not data:
        raise HTTPException(status_code=404, detail="Mesh memory not found")
    return MeshMemoryResponse(**data[0])

def signal_scores_endpoint(mesh_id: int, session: Session = Depends(get_session)) -> List[SignalScoresResponse]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE mesh_id = {mesh_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    return [SignalScoresResponse(**item) for item in response.json()]

def get_score_disputes_endpoint(mesh_id: int, session: Session = Depends(get_session)) -> List[ScoreDisputesResponse]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM McpScoreDispute WHERE mesh_id = {mesh_id}"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching score disputes")
    return [ScoreDisputesResponse(**item) for item in response.json()]

def _run_self_test():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    test_engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test get_mesh_memory_endpoint
    try:
        mesh_memory = get_mesh_memory_endpoint(1)
        print("Mesh Memory Test:", mesh_memory)
    except HTTPException as e:
        print("Mesh Memory Test Error:", e.detail)

    # Test signal_scores_endpoint
    try:
        signal_scores = signal_scores_endpoint(1)
        print("Signal Scores Test:", signal_scores)
    except HTTPException as e:
        print("Signal Scores Test Error:", e.detail)

    # Test get_score_disputes_endpoint
    try:
        score_disputes = get_score_disputes_endpoint(1)
        print("Score Disputes Test:", score_disputes)
    except HTTPException as e:
        print("Score Disputes Test Error:", e.detail)

    print("PASS")

if __name__ == "__main__":
    _run_self_test()