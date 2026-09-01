"""Auto-emitted service package."""
from typing import Any, Dict, List, Optional
from functools import lru_cache

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute

router = APIRouter()


class MeshMemoryResponse(BaseModel):
    id: str
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class SignalScoreResponse(BaseModel):
    id: str
    score: float
    signal_type: str
    org_id: str


@lru_cache()
def get_mesh_memory_endpoint() -> str:
    return "http://127.0.0.1:8772/query"


def mesh_memory_endpoint(
    query: Dict[str, Any],
    session=None,
) -> List[MeshMemoryResponse]:
    """Query mesh_memory store."""
    response = requests.post(
        f"{get_mesh_memory_endpoint()}/mesh_memory",
        json=query,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return [MeshMemoryResponse(**item) for item in data.get("results", [])]


def get_mesh_memory_by_id(memory_id: str, session=None) -> Optional[MeshMemoryResponse]:
    """Get mesh memory by ID."""
    query = {"filter": {"id": memory_id}}
    results = mesh_memory_endpoint(query, session)
    return results[0] if results else None


def signal_scores_endpoint(
    org_id: str,
    signal_type: Optional[str] = None,
    session=None,
) -> List[SignalScoreResponse]:
    """Query mcp_signal_scores store."""
    query = {"filter": {"org_id": org_id}}
    if signal_type:
        query["filter"]["signal_type"] = signal_type
    response = requests.post(
        f"{get_mesh_memory_endpoint()}/mcp_signal_scores",
        json=query,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return [SignalScoreResponse(**item) for item in data.get("results", [])]


@router.get("/health")
def health_check():
    return {"status": "ok"}


class Users(BaseModel):
    id: str
    email: str
    org_id: str

    class Config:
        from_attributes = True


class ScoreDisputes(BaseModel):
    id: str
    org_id: str
    score_id: str
    status: str

    class Config:
        from_attributes = True


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    import uvicorn

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session() -> Session:
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(router)

    from app.db import get_session
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/__test__")
    def run_test():
        try:
            health_check()
            return {"result": "PASS"}
        except Exception as e:
            return {"result": f"FAIL: {e}"}

    uvicorn.run(app, host="127.0.0.1", port=0)