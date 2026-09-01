from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org
from typing import List, Optional
import requests

class ServicePackage:
    def __init__(self):
        self.base_url = "http://127.0.0.1:8772"

    def mesh_memory_endpoint(self, session: Session = Depends(get_session)):
        try:
            response = requests.get(f"{self.base_url}/mesh_memory", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_mesh_memory_by_id(self, memory_id: int, session: Session = Depends(get_session)):
        try:
            response = requests.get(f"{self.base_url}/mesh_memory/{memory_id}", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    def signal_scores_endpoint(self, session: Session = Depends(get_session)):
        try:
            response = requests.get(f"{self.base_url}/signal_scores", timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=str(e))

    def users_endpoint(self, session: Session = Depends(get_session)):
        try:
            users = session.query(User).all()
            return [{"id": user.id, "name": user.name} for user in users]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def get_score_disputes_endpoint(self, session: Session = Depends(get_session)):
        try:
            disputes = session.query(McpScoreDispute).all()
            return [{"id": dispute.id, "score_id": dispute.score_id} for dispute in disputes]
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def test_self(self, session: Session = Depends(get_session)):
        try:
            # Test mesh_memory_endpoint
            mesh_memory = self.mesh_memory_endpoint(session)
            if not mesh_memory:
                raise Exception("mesh_memory_endpoint failed")

            # Test get_mesh_memory_by_id
            if not mesh_memory:
                memory_id = 1
            else:
                memory_id = mesh_memory[0]["id"]
            memory = self.get_mesh_memory_by_id(memory_id, session)
            if not memory:
                raise Exception("get_mesh_memory_by_id failed")

            # Test signal_scores_endpoint
            signal_scores = self.signal_scores_endpoint(session)
            if not signal_scores:
                raise Exception("signal_scores_endpoint failed")

            # Test users_endpoint
            users = self.users_endpoint(session)
            if not users:
                raise Exception("users_endpoint failed")

            # Test get_score_disputes_endpoint
            disputes = self.get_score_disputes_endpoint(session)
            if not disputes:
                raise Exception("get_score_disputes_endpoint failed")

            return "PASS"
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    app = FastAPI()
    service_package = ServicePackage()

    @app.get("/test")
    async def test():
        return service_package.test_self()

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)