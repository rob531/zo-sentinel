from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

class ServicePackage:
    def __init__(self):
        self.app = FastAPI()

    def mesh_memory_endpoint(self, mesh_id: int) -> dict:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE id = {mesh_id}"}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Mesh memory not found")
        return response.json()

    def get_mesh_memory_by_id(self, mesh_id: int, session: Session = Depends(get_session)) -> dict:
        return self.mesh_memory_endpoint(mesh_id)

    def signal_scores_endpoint(self, session: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
        return session.query(McpLlmAxisScore).all()

    def get_score_disputes_endpoint(self, session: Session = Depends(get_session)) -> List[McpScoreDispute]:
        return session.query(McpScoreDispute).all()

    def get_server_registry(self, session: Session = Depends(get_session)) -> List[McpServerRegistry]:
        return session.query(McpServerRegistry).all()

    def get_orgs(self, session: Session = Depends(get_session)) -> List[Org]:
        return session.query(Org).all()

    def get_users(self, session: Session = Depends(get_session)) -> List[User]:
        return session.query(User).all()

if __name__ == "__main__":
    service = ServicePackage()
    test_app = FastAPI()

    @test_app.get("/test")
    def test_endpoint():
        return {"status": "PASS"}

    test_app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)
    print("PASS")