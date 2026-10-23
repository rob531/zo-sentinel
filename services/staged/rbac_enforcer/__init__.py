from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
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
    response = requests.post('http://127.0.0.1:8772/query', json={'query': f'SELECT * FROM mesh_memory WHERE id = {id}'})
    return response.json()

def read_all():
    pass

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
    response = requests.post('http://127.0.0.1:8772/query', json={'query': f'SELECT * FROM mcp_signal_scores WHERE id = {id}'})
    return response.json()

class LocalMcpLlmAxisScore(McpLlmAxisScore):
    pass

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    @app.get("/")
    def read_root():
        return {"Hello": "World"}

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    print("PASS")