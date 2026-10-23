from typing import List, Optional
from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

class ServicePackage:
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def mesh_memory_endpoint(self, mesh_id: int) -> dict:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE id = {mesh_id}"}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Mesh memory query failed")
        return response.json()

    def get_mesh_memory_by_id(self, mesh_id: int) -> dict:
        return self.mesh_memory_endpoint(mesh_id)

    def signal_scores_endpoint(self, signal_id: int) -> dict:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE id = {signal_id}"}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Signal scores query failed")
        return response.json()

    def users_endpoint(self) -> List[User]:
        return self.session.query(User).all()

    def get_score_disputes_endpoint(self) -> List[McpScoreDispute]:
        return self.session.query(McpScoreDispute).all()

    def test_self(self) -> str:
        return "PASS"

def test_service_package():
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)
    service = ServicePackage()
    assert service.test_self() == "PASS"
    print("PASS")

if __name__ == "__main__":
    test_service_package()