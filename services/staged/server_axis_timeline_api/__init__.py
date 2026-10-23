from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, Org, User
from pydantic import BaseModel
import requests

class MeshMemory(BaseModel):
    id: int
    content: str
    org_id: int

class SignalScores(BaseModel):
    id: int
    score: float
    org_id: int

class UserRead(BaseModel):
    id: int
    username: str
    org_id: int

class ScoreDispute(BaseModel):
    id: int
    score_id: int
    reason: str
    org_id: int

def get_mesh_memory_by_id(id: int, session: Session = Depends(get_session)) -> Optional[MeshMemory]:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"})
    if response.status_code != 200:
        return None
    data = response.json()
    if not data:
        return None
    return MeshMemory(**data[0])

def mesh_memory_endpoint():
    return {"message": "Mesh memory endpoint"}

def signal_scores_endpoint():
    return {"message": "Signal scores endpoint"}

def users_endpoint():
    return {"message": "Users endpoint"}

def get_score_disputes_endpoint():
    return {"message": "Score disputes endpoint"}

def test_self():
    return {"message": "Self test"}

def run_self_test():
    return {"message": "Run self test"}

def test_service_package():
    return {"message": "Test service package"}

if __name__ == "__main__":
    app = FastAPI()

    @app.get("/test")
    def test():
        return {"message": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)