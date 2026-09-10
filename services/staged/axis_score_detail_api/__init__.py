from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User
import requests

def get_server_registries(db: Session = Depends(get_session)) -> List[McpServerRegistry]:
    return db.query(McpServerRegistry).all()

def get_mesh_memory_endpoint() -> dict:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "mesh_memory"}, timeout=10)
    response.raise_for_status()
    return response.json()

def mesh_memory_endpoint_get() -> dict:
    return get_mesh_memory_endpoint()

def mesh_scores_endpoint() -> dict:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "mcp_signal_scores"}, timeout=10)
    response.raise_for_status()
    return response.json()

def signal_scores_endpoint() -> dict:
    return mesh_scores_endpoint()

def get_users(db: Session = Depends(get_session)) -> List[User]:
    return db.query(User).all()

def users_endpoint() -> dict:
    response = requests.get("http://127.0.0.1:8772/query", params={"table": "users"}, timeout=10)
    response.raise_for_status()
    return response.json()

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
    try:
        # Test database connection
        db = next(get_session())
        db.query(McpServerRegistry).first()
        db.close()

        # Test mesh_memory endpoint
        get_mesh_memory_endpoint()

        # Test signal_scores endpoint
        signal_scores_endpoint()

        return "PASS"
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: next(get_session())

    @app.get("/test")
    def test():
        return {"status": run_self_test()}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)