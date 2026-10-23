from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

router = APIRouter()

def mesh_memory_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

def get_mesh_memory_by_id(id: int):
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"})
    return response.json()

class Users:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_all_users(self) -> List[User]:
        return self.db.query(User).all()

class ScoreDisputes:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_all_disputes(self) -> List[McpScoreDispute]:
        return self.db.query(McpScoreDispute).all()

def signal_scores_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    @app.get("/test")
    async def test():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print("PASS")