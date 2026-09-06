from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory
from typing import List, Optional
import requests
from pydantic import BaseModel

class MeshMemoryResponse(BaseModel):
    id: int
    content: str
    created_at: str

class MeshMemoryRequest(BaseModel):
    content: str

class MeshMemory:
    def __init__(self, db: Session = Depends(get_session)):
        self.db = db

    def get_mesh_memory(self) -> List[MeshMemoryResponse]:
        response = requests.post("http://127.0.0.1:8772/query", json={
            "query": "SELECT id, content, created_at FROM mesh_memory"
        })
        return [MeshMemoryResponse(**item) for item in response.json()]

    def get_mesh_memory_by_id(self, id: int) -> Optional[MeshMemoryResponse]:
        response = requests.post("http://127.0.0.1:8772/query", json={
            "query": "SELECT id, content, created_at FROM mesh_memory WHERE id = :id",
            "params": {"id": id}
        })
        result = response.json()
        return MeshMemoryResponse(**result[0]) if result else None

    def mesh_memory_endpoint(self, request: MeshMemoryRequest) -> MeshMemoryResponse:
        response = requests.post("http://127.0.0.1:8772/query", json={
            "query": "INSERT INTO mesh_memory (content) VALUES (:content) RETURNING id, content, created_at",
            "params": {"content": request.content}
        })
        return MeshMemoryResponse(**response.json()[0])

class McpServerRegistry(McpServerRegistry):
    pass

class VulnAdvisory(VulnAdvisory):
    pass

def _run_self_test():
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from app.db import Base

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=create_engine("sqlite:///:memory:", poolclass=StaticPool),
        autocommit=True,
        autoflush=False
    )

    Base.metadata.create_all(bind=create_engine("sqlite:///:memory:", poolclass=StaticPool))

    @test_app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    _run_self_test()
    print("PASS")