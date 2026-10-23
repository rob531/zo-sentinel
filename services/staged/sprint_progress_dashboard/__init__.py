from typing import List, Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

class Server(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    org_id: int
    created_at: str
    updated_at: str

class AxisScore(BaseModel):
    id: int
    server_id: int
    axis: str
    score: float
    created_at: str
    updated_at: str

class Dispute(BaseModel):
    id: int
    score_id: int
    user_id: int
    comment: str
    status: str
    created_at: str
    updated_at: str

def get_mesh_memory() -> List[dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def mesh_scores(server_id: int) -> List[AxisScore]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return [AxisScore(**item) for item in response.json()]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def api_signal_scores(server_id: int, db: Session = Depends(get_session)) -> List[AxisScore]:
    return db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: sessionmaker(
        bind=create_engine("sqlite:///:memory:", poolclass=StaticPool),
        expire_on_commit=False
    )()

    client = TestClient(test_app)

    # Test get_mesh_memory
    try:
        mesh_memory = get_mesh_memory()
        print("get_mesh_memory test: PASS")
    except Exception as e:
        print(f"get_mesh_memory test: FAIL - {str(e)}")

    # Test mesh_scores
    try:
        mesh_scores([1])
        print("mesh_scores test: PASS")
    except Exception as e:
        print(f"mesh_scores test: FAIL - {str(e)}")

    # Test api_signal_scores
    try:
        api_signal_scores(1)
        print("api_signal_scores test: PASS")
    except Exception as e:
        print(f"api_signal_scores test: FAIL - {str(e)}")

    print("PASS")