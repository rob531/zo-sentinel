from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User
from typing import List, Optional
import requests
from pydantic import BaseModel

class ServerResponse(BaseModel):
    id: int
    hostname: str
    risk_level: str
    last_scored: str

class UserRead(BaseModel):
    id: int
    username: str
    email: str

class McpScoreDisputeService:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_disputes(self, server_id: int) -> List[McpScoreDispute]:
        return self.db.query(McpScoreDispute).filter(McpScoreDispute.server_id == server_id).all()

class Users:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_users(self) -> List[UserRead]:
        users = self.db.query(User).all()
        return [UserRead(id=user.id, username=user.username, email=user.email) for user in users]

def get_mesh_memory_endpoint():
    def endpoint():
        response = requests.post("http://127.0.0.1:8772/query", json={
            "query": "SELECT * FROM mesh_memory"
        })
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
        return response.json()
    return endpoint

def get_score_disputes_endpoint():
    def endpoint(server_id: int, db: Session = Depends(get_session)):
        disputes = McpScoreDisputeService(db).get_disputes(server_id)
        return [{"id": dispute.id, "server_id": dispute.server_id, "axis": dispute.axis, "disputed_score": dispute.disputed_score} for dispute in disputes]
    return endpoint

def mesh_scores_endpoint():
    def endpoint():
        response = requests.post("http://127.0.0.1:8772/query", json={
            "query": "SELECT * FROM mcp_signal_scores"
        })
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Error fetching mesh scores")
        return response.json()
    return endpoint

def dummy_post_api():
    def endpoint():
        return {"message": "Dummy POST API"}
    return endpoint

def get_users():
    def endpoint(db: Session = Depends(get_session)):
        users = Users(db).get_users()
        return users
    return endpoint

def run_self_test():
    app = FastAPI()

    @app.get("/self_test")
    def self_test():
        return {"status": "PASS"}

    return app

if __name__ == "__main__":
    test_app = run_self_test()
    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)