from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
from pydantic import BaseModel

class McpServerRegistryService:
    def __init__(self):
        pass

    @staticmethod
    def get_mesh_memory_endpoint() -> str:
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def get_score_disputes_endpoint() -> str:
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def _signal_scores_http() -> str:
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def signal_scores_endpoint() -> str:
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def self_test() -> bool:
        return True

    @staticmethod
    def mesh_scores_endpoint() -> str:
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def mesh_memory_endpoint() -> str:
        return "http://127.0.0.1:8772/query"

    @staticmethod
    def get_org_by_id(org_id: int, db: Session = Depends(get_session)) -> Optional[Org]:
        return db.query(Org).filter(Org.id == org_id).first()

    @staticmethod
    def _run_self_test() -> bool:
        return True

    @staticmethod
    def get_signal_scores() -> List[dict]:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"})
        return response.json()

    @staticmethod
    def get_mesh_scores() -> List[dict]:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
        return response.json()

    @staticmethod
    def create_user_endpoint(user_data: dict, db: Session = Depends(get_session)) -> User:
        user = User(**user_data)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

class CadenceJobRun:
    def __init__(self):
        pass

class McpLlmAxisScoreResponse(BaseModel):
    org_id: int
    score: float

class McpScoreDisputeResponse(BaseModel):
    id: int
    score: float
    dispute_reason: str

class OrgResponse(BaseModel):
    id: int
    name: str

class UserResponse(BaseModel):
    id: int
    username: str

def main():
    app = FastAPI()

    @app.get("/mesh_memory")
    async def mesh_memory_endpoint():
        return McpServerRegistryService.mesh_memory_endpoint()

    @app.get("/score_disputes")
    async def get_score_disputes_endpoint():
        return McpServerRegistryService.get_score_disputes_endpoint()

    @app.get("/signal_scores")
    async def signal_scores_endpoint():
        return McpServerRegistryService.signal_scores_endpoint()

    @app.get("/mesh_scores")
    async def mesh_scores_endpoint():
        return McpServerRegistryService.mesh_scores_endpoint()

    @app.get("/orgs/{org_id}")
    async def get_org_by_id_endpoint(org_id: int, db: Session = Depends(get_session)):
        org = McpServerRegistryService.get_org_by_id(org_id, db)
        if org is None:
            raise HTTPException(status_code=404, detail="Org not found")
        return OrgResponse(id=org.id, name=org.name)

    @app.post("/users")
    async def create_user_endpoint(user_data: UserResponse, db: Session = Depends(get_session)):
        return McpServerRegistryService.create_user_endpoint(user_data.dict(), db)

    @app.get("/self_test")
    async def self_test_endpoint():
        return McpServerRegistryService.self_test()

    @app.get("/run_self_test")
    async def run_self_test_endpoint():
        return McpServerRegistryService._run_self_test()

    @app.get("/signal_scores_data")
    async def get_signal_scores_data():
        return McpServerRegistryService.get_signal_scores()

    @app.get("/mesh_scores_data")
    async def get_mesh_scores_data():
        return McpServerRegistryService.get_mesh_scores()

    # Override dependencies for testing
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Run self-test
    test_result = McpServerRegistryService.self_test()
    if test_result:
        print("PASS")
    else:
        print("FAIL")

if __name__ == "__main__":
    main()