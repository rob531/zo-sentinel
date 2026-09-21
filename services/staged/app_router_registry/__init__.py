from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from typing import List, Optional
import requests

router = APIRouter()

def reset_quarantine_api():
    return {"message": "Quarantine API reset"}

def _run_self_test():
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    print("PASS")

def mesh_memory_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")

def get_mesh_memory_by_id(id: int):
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory by ID")

def read_all(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def signal_scores_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")

class LocalMcpLlmAxisScore(McpLlmAxisScore):
    pass

def mesh_scores_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_llm_axis_scores"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")

def api_signal_scores():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")

def get_mesh_memory():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")

def get_signal_scores_by_id(id: int):
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mcp_signal_scores WHERE id = {id}"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores by ID")

if __name__ == "__main__":
    _run_self_test()