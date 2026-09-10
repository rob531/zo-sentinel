from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
import json

app = FastAPI()

def get_mcp_server_registry(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_mcp_llm_axis_scores(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def get_mcp_score_disputes(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def get_orgs(db: Session = Depends(get_session)):
    return db.query(Org).all()

def get_users(db: Session = Depends(get_session)):
    return db.query(User).all()

def query_mesh_memory(endpoint: str, params: dict):
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=params,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from app import dependency_overrides
    app.dependency_overrides[get_session] = lambda: get_session()

    # Self-test
    try:
        # Test app.db imports
        db = next(get_session())
        assert db is not None

        # Test model imports
        assert McpServerRegistry
        assert McpLlmAxisScore
        assert McpScoreDispute
        assert Org
        assert User

        # Test mesh query
        test_params = {"query": "SELECT * FROM mesh_memory LIMIT 1"}
        test_response = query_mesh_memory("mesh_memory", test_params)
        assert isinstance(test_response, dict)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")