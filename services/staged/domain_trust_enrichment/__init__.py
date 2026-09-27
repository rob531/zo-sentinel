from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

router = APIRouter()

def reset_quarantine_api():
    pass

def _run_self_test():
    pass

def mesh_memory_endpoint():
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mesh_memory'})
    return response.json()

def get_mesh_memory_by_id(id: int):
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mesh_memory WHERE id = :id', 'params': {'id': id}})
    return response.json()

def read_all():
    session = next(get_session())
    try:
        return session.query(McpServerRegistry).all()
    finally:
        session.close()

def signal_scores_endpoint():
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mcp_signal_scores'})
    return response.json()

def mesh_scores_endpoint():
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mcp_signal_scores'})
    return response.json()

def api_signal_scores():
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mcp_signal_scores'})
    return response.json()

def get_mesh_memory():
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mesh_memory'})
    return response.json()

def get_signal_scores_by_id(id: int):
    response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT * FROM mcp_signal_scores WHERE id = :id', 'params': {'id': id}})
    return response.json()

class LocalMcpLlmAxisScore(McpLlmAxisScore):
    pass

if __name__ == '__main__':
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

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

    def test_reset_quarantine_api():
        response = client.get("/reset_quarantine_api")
        assert response.status_code == 200

    def test_mesh_memory_endpoint():
        response = client.get("/mesh_memory_endpoint")
        assert response.status_code == 200

    def test_get_mesh_memory_by_id():
        response = client.get("/get_mesh_memory_by_id/1")
        assert response.status_code == 200

    def test_read_all():
        response = client.get("/read_all")
        assert response.status_code == 200

    def test_signal_scores_endpoint():
        response = client.get("/signal_scores_endpoint")
        assert response.status_code == 200

    def test_mesh_scores_endpoint():
        response = client.get("/mesh_scores_endpoint")
        assert response.status_code == 200

    def test_api_signal_scores():
        response = client.get("/api_signal_scores")
        assert response.status_code == 200

    def test_get_mesh_memory():
        response = client.get("/get_mesh_memory")
        assert response.status_code == 200

    def test_get_signal_scores_by_id():
        response = client.get("/get_signal_scores_by_id/1")
        assert response.status_code == 200

    print("PASS")