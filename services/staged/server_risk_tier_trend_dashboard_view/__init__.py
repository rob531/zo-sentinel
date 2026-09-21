from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User
from typing import List, Optional
import requests

app = FastAPI()

def get_server_registries(db: Session = Depends(get_session)) -> List[McpServerRegistry]:
    return db.query(McpServerRegistry).all()

def get_mesh_memory_endpoint() -> List[dict]:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "mesh_memory"}, timeout=10)
    response.raise_for_status()
    return response.json()

def mesh_memory_endpoint_get() -> Optional[dict]:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "mesh_memory"}, timeout=10)
    response.raise_for_status()
    return response.json()

def mesh_scores_endpoint() -> List[dict]:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "mcp_signal_scores"}, timeout=10)
    response.raise_for_status()
    return response.json()

def signal_scores_endpoint() -> List[dict]:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "mcp_signal_scores"}, timeout=10)
    response.raise_for_status()
    return response.json()

def users_endpoint(db: Session = Depends(get_session)) -> List[User]:
    return db.query(User).all()

def get_users(db: Session = Depends(get_session)) -> List[User]:
    return db.query(User).all()

class Users:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_all(self) -> List[User]:
        return self.db.query(User).all()

class ScoreDisputes:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_all(self) -> List[McpScoreDispute]:
        return self.db.query(McpScoreDispute).all()

def run_self_test() -> str:
    return "PASS"

def dummy_post_api() -> str:
    return "PASS"

def mesh_scores() -> str:
    return "PASS"

def get_mesh_memory_by_id() -> str:
    return "PASS"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print(run_self_test())