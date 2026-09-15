"""Service package endpoint definitions."""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
)

import requests


class MeshMemoryResponse(BaseModel):
    id: str
    content: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class ScoreDisputeResponse(BaseModel):
    id: str
    server_id: str
    axis: str
    score: float
    reason: str
    status: str


class ServerRegistryResponse(BaseModel):
    id: str
    name: str
    url: str
    status: str


router = APIRouter()


def get_mesh_memory_endpoint() -> str:
    return "http://127.0.0.1:8772/mesh_memory"


def get_score_disputes_endpoint() -> str:
    return "http://127.0.0.1:8772/score_disputes"


def get_server_registries_endpoint() -> str:
    return "http://127.0.0.1:8772/server_registries"


def get_signal_scores_endpoint() -> str:
    return "http://127.0.0.1:8772/signal_scores"


def _get_mesh_memory_by_id(mesh_memory_id: str) -> Optional[Dict[str, Any]]:
    endpoint = get_mesh_memory_endpoint()
    try:
        resp = requests.get(
            f"{endpoint}/{mesh_memory_id}",
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception:
        return None


def get_mesh_memory_by_id(mesh_memory_id: str) -> Optional[Dict[str, Any]]:
    return _get_mesh_memory_by_id(mesh_memory_id)


def mesh_memory_endpoint_get(mesh_memory_id: str) -> Optional[MeshMemoryResponse]:
    data = _get_mesh_memory_by_id(mesh_memory_id)
    if data:
        return MeshMemoryResponse(**data)
    return None


def mesh_memory_endpoint(mesh_memory_id: str) -> Optional[MeshMemoryResponse]:
    return mesh_memory_endpoint_get(mesh_memory_id)


def _get_score_disputes_by_server(server_id: str) -> List[Dict[str, Any]]:
    endpoint = get_score_disputes_endpoint()
    try:
        resp = requests.get(
            f"{endpoint}?server_id={server_id}",
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        return []


def signal_scores_endpoint(server_id: str) -> List[Dict[str, Any]]:
    endpoint = get_signal_scores_endpoint()
    try:
        resp = requests.get(
            f"{endpoint}?server_id={server_id}",
            timeout=30
        )
        if resp.status_code == 200:
            return resp.json()
        return []
    except Exception:
        return []


def recency_report(server_id: str) -> Dict[str, Any]:
    scores = signal_scores_endpoint(server_id)
    if not scores:
        return {"server_id": server_id, "recent_count": 0}
    return {
        "server_id": server_id,
        "recent_count": len(scores),
        "latest": scores[0] if scores else None
    }


def get_server_registries(session: Session = Depends(get_session)) -> List[McpServerRegistry]:
    result = session.execute(select(McpServerRegistry))
    return list(result.scalars().all())


def run_self_test() -> bool:
    """Self-test for service package endpoints."""
    try:
        from fastapi import FastAPI
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool
        )

        from app.models import Base
        Base.metadata.create_all(bind=engine)

        TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        def override_get_session():
            db = TestingSessionLocal()
            try:
                yield db
            finally:
                db.close()

        that_app = FastAPI()

        @that_app.get("/test/mesh_memory/{mesh_id}")
        async def test_mesh_memory(mesh_id: str):
            return mesh_memory_endpoint(mesh_id)

        @that_app.get("/test/signal_scores")
        async def test_signal_scores(server_id: str):
            return signal_scores_endpoint(server_id)

        that_app.dependency_overrides[get_session] = override_get_session

        return True
    except Exception as e:
        print(f"SELF-TEST FAIL: {e}")
        return False


if __name__ == "__main__":
    if run_self_test():
        print("PASS")
    else:
        print("FAIL")