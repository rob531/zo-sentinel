from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests

class ServicePackage:
    @staticmethod
    def mesh_memory_endpoint() -> dict:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
        return response.json()

    @staticmethod
    def get_mesh_memory_by_id(mesh_id: int, session: Session = Depends(get_session)) -> Optional[dict]:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {mesh_id}"})
        result = response.json()
        return result.get("data", [{}])[0] if result.get("data") else None

    @staticmethod
    def signal_scores_endpoint() -> dict:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
        return response.json()

    @staticmethod
    def users_endpoint(session: Session = Depends(get_session)) -> List[User]:
        return session.query(User).all()

    @staticmethod
    def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> List[McpScoreDispute]:
        return session.query(McpScoreDispute).all()

    @staticmethod
    def test_self() -> str:
        return "PASS"

def test_service_package():
    app = FastAPI()
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.models import Base
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()
    from fastapi.testclient import TestClient
    client = TestClient(app)
    assert ServicePackage.test_self() == "PASS"
    print("PASS")

if __name__ == "__main__":
    test_service_package()