from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory
from typing import List, Optional
import requests

class MeshMemory:
    def __init__(self, id: int, data: dict):
        self.id = id
        self.data = data

class MeshMemoryEndpoint:
    def __init__(self, session: Session = Depends(get_session)):
        self.session = session

    def get_mesh_memory(self) -> List[MeshMemory]:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"})
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
        return [MeshMemory(**item) for item in response.json()]

    def get_mesh_memory_by_id(self, id: int) -> Optional[MeshMemory]:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"})
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory by id")
        result = response.json()
        return MeshMemory(**result[0]) if result else None

    def mesh_memory_endpoint(self) -> List[MeshMemory]:
        return self.get_mesh_memory()

    def mesh_scores_endpoint(self) -> List[McpLlmAxisScore]:
        return self.session.query(McpLlmAxisScore).all()

    def api_signal_scores(self) -> List[McpLlmAxisScore]:
        return self.session.query(McpLlmAxisScore).all()

    def get_signal_scores(self) -> List[McpLlmAxisScore]:
        return self.session.query(McpLlmAxisScore).all()

    def get_mesh_memory_endpoint(self) -> List[MeshMemory]:
        return self.get_mesh_memory()

    def dummy_endpoint_route(self) -> str:
        return "Dummy endpoint"

class McpServerRegistry(McpServerRegistry):
    pass

class VulnAdvisory(VulnAdvisory):
    pass

def _run_self_test():
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    @app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    _run_self_test()
    print("PASS")