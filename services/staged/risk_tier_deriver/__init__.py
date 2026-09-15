from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User, McpServerRegistry, McpScoreDispute, McpLlmAxisScore, OrgService, UserService
from typing import List, Optional
import requests

def get_mesh_scores() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
    return response.json()

def mesh_memory_endpoint() -> List[dict]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
    return response.json()

def mesh_scores_endpoint() -> List[dict]:
    return get_mesh_scores()

def get_users(db: Session = Depends(get_session)) -> List[User]:
    return db.query(User).all()

def dummy_post_api(data: dict) -> dict:
    return {"status": "success", "data": data}

def get_mesh_memory_endpoint() -> List[dict]:
    return mesh_memory_endpoint()

def get_score_disputes_endpoint(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
    return db.query(McpScoreDispute).all()

def signal_scores_endpoint() -> List[dict]:
    return get_mesh_scores()

def mesh_scores() -> List[dict]:
    return get_mesh_scores()

def test() -> str:
    return "test"

def get_mesh_memory_endpoint() -> List[dict]:
    return mesh_memory_endpoint()

def get_mesh_scores() -> List[dict]:
    return get_mesh_scores()

def get_users(db: Session = Depends(get_session)) -> List[User]:
    return db.query(User).all()

def dummy_post_api(data: dict) -> dict:
    return {"status": "success", "data": data}

def get_score_disputes_endpoint(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
    return db.query(McpScoreDispute).all()

def signal_scores_endpoint() -> List[dict]:
    return get_mesh_scores()

def mesh_scores() -> List[dict]:
    return get_mesh_scores()

def test() -> str:
    return "test"

def get_mesh_memory_endpoint() -> List[dict]:
    return mesh_memory_endpoint()

if __name__ == "__main__":
    app = FastAPI()

    @app.get("/mesh_scores")
    async def get_mesh_scores_endpoint():
        return get_mesh_scores()

    @app.get("/mesh_memory")
    async def get_mesh_memory_endpoint():
        return mesh_memory_endpoint()

    @app.get("/users")
    async def get_users_endpoint(db: Session = Depends(get_session)):
        return get_users(db)

    @app.post("/dummy_post")
    async def dummy_post_endpoint(data: dict):
        return dummy_post_api(data)

    @app.get("/score_disputes")
    async def get_score_disputes_endpoint(db: Session = Depends(get_session)):
        return get_score_disputes_endpoint(db)

    @app.get("/signal_scores")
    async def signal_scores_endpoint():
        return signal_scores_endpoint()

    @app.get("/mesh_scores")
    async def mesh_scores_endpoint():
        return mesh_scores()

    @app.get("/test")
    async def test_endpoint():
        return test()

    @app.get("/mesh_memory")
    async def get_mesh_memory_endpoint():
        return get_mesh_memory_endpoint()

    print("PASS")