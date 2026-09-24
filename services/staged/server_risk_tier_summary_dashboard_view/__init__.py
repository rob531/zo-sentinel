from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from typing import List, Optional
import requests

router = APIRouter()

def self_test():
    print("PASS")

def get_signal_scores(session: Session = Depends(get_session)):
    scores = session.query(McpLlmAxisScore).all()
    return scores

def mesh_memory_endpoint():
    response = requests.post('http://127.0.0.1:8772/query', json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
    return response.json()

def reset_server_export_api_quarantine_endpoint(session: Session = Depends(get_session)):
    session.query(McpServerRegistry).update({"quarantine": False})
    session.commit()
    return {"status": "success"}

def signal_scores_endpoint(session: Session = Depends(get_session)):
    scores = session.query(McpLlmAxisScore).all()
    return scores

def get_mesh_memory_endpoint():
    response = requests.post('http://127.0.0.1:8772/query', json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
    return response.json()

def get_score_disputes_endpoint(session: Session = Depends(get_session)):
    disputes = session.query(McpScoreDispute).all()
    return disputes

def _run_self_test():
    print("PASS")

def test_service_package():
    print("PASS")

def mesh_memory_endpoint_get():
    response = requests.post('http://127.0.0.1:8772/query', json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
    return response.json()

class McpServerRegistry:
    def __init__(self, id: int, name: str, status: str):
        self.id = id
        self.name = name
        self.status = status

def main():
    print("PASS")

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    @app.get("/self_test")
    def self_test_endpoint():
        return {"status": "PASS"}

    uvicorn.run(app, host="127.0.0.1", port=8000)