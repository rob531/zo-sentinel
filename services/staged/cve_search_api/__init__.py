from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

class OrgService:
    @staticmethod
    async def get_orgs(db: Session = Depends(get_session)) -> List[Org]:
        return db.query(Org).all()

class UserService:
    @staticmethod
    async def get_users(db: Session = Depends(get_session)) -> List[User]:
        return db.query(User).all()

def mesh_scores_endpoint() -> List[McpLlmAxisScore]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM McpLlmAxisScore"})
    return response.json()

def get_mesh_memory_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

def get_score_disputes_endpoint() -> List[McpScoreDispute]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM McpScoreDispute"})
    return response.json()

def signal_scores_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def get_signal_scores() -> List[dict]:
    return signal_scores_endpoint()

def _run_self_test():
    app = FastAPI()

    @app.get("/test")
    async def test():
        return {"status": "PASS"}

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    _run_self_test()