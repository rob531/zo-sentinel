from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User
from typing import List, Optional
from pydantic import BaseModel
import requests

class MeshMemoryEndpointResponse(BaseModel):
    id: int
    data: dict

class ScoreDisputeResponse(BaseModel):
    id: int
    user_id: int
    score_id: int
    reason: str
    status: str

class UserResponse(BaseModel):
    id: int
    clerk_id: str
    clerk_created_at: str

class McpServerRegistryResponse(BaseModel):
    id: int
    host: str
    port: int
    status: str

class McpLlmAxisScoreResponse(BaseModel):
    id: int
    score: float
    axis: str
    model_version: str

def mesh_memory_endpoint(id: int) -> MeshMemoryEndpointResponse:
    response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {id}"})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    return MeshMemoryEndpointResponse(**response.json()[0])

def get_mesh_memory_by_id(id: int, session: Session = Depends(get_session)) -> MeshMemoryEndpointResponse:
    return mesh_memory_endpoint(id)

def get_score_disputes_endpoint(session: Session = Depends(get_session)) -> List[ScoreDisputeResponse]:
    disputes = session.query(McpScoreDispute).all()
    return [ScoreDisputeResponse(id=d.id, user_id=d.user_id, score_id=d.score_id, reason=d.reason, status=d.status) for d in disputes]

def get_users_endpoint(session: Session = Depends(get_session)) -> List[UserResponse]:
    users = session.query(User).all()
    return [UserResponse(id=u.id, clerk_id=u.clerk_id, clerk_created_at=u.clerk_created_at) for u in users]

def get_mcp_server_registry_endpoint(session: Session = Depends(get_session)) -> List[McpServerRegistryResponse]:
    servers = session.query(McpServerRegistry).all()
    return [McpServerRegistryResponse(id=s.id, host=s.host, port=s.port, status=s.status) for s in servers]

def get_mcp_llm_axis_scores_endpoint(session: Session = Depends(get_session)) -> List[McpLlmAxisScoreResponse]:
    scores = session.query(McpLlmAxisScore).all()
    return [McpLlmAxisScoreResponse(id=s.id, score=s.score, axis=s.axis, model_version=s.model_version) for s in scores]

if __name__ == "__main__":
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: Session(bind=None, autocommit=False, autoflush=False)

    @app.get("/mesh_memory/{id}")
    async def test_mesh_memory_endpoint(id: int):
        return mesh_memory_endpoint(id)

    @app.get("/score_disputes")
    async def test_score_disputes_endpoint():
        return get_score_disputes_endpoint()

    @app.get("/users")
    async def test_users_endpoint():
        return get_users_endpoint()

    @app.get("/McpServerRegistry")
    async def test_mcp_server_registry_endpoint():
        return get_mcp_server_registry_endpoint()

    @app.get("/McpLlmAxisScore")
    async def test_mcp_llm_axis_scores_endpoint():
        return get_mcp_llm_axis_scores_endpoint()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print("PASS")