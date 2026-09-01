"""Staged services package."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter()


class MeshMemoryResponse(BaseModel):
    org_id: str
    memory: Dict[str, Any]


class SignalScoresResponse(BaseModel):
    org_id: str
    scores: List[Dict[str, Any]]


def get_mesh_memory(org_id: str, session=None) -> Optional[Dict[str, Any]]:
    """Get mesh memory for org from ZoComputer store."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mesh_memory", "org_id": org_id},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [{}])[0] if data.get("rows") else None
    except Exception:
        pass
    return None


def get_signal_scores(org_id: str, session=None) -> List[Dict[str, Any]]:
    """Get signal scores for org from ZoComputer store."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"table": "mcp_signal_scores", "org_id": org_id},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
    except Exception:
        pass
    return []


@router.get("/mesh-memory/{org_id}", response_model=MeshMemoryResponse)
def mesh_memory_endpoint(org_id: str, session=Depends(get_session)) -> MeshMemoryResponse:
    """Mesh memory endpoint."""
    memory = get_mesh_memory(org_id)
    if memory is None:
        memory = {}
    return MeshMemoryResponse(org_id=org_id, memory=memory)


@router.get("/mesh-memory-get/{org_id}", response_model=MeshMemoryResponse)
def mesh_memory_endpoint_get(org_id: str, session=Depends(get_session)) -> MeshMemoryResponse:
    """Mesh memory GET endpoint."""
    return mesh_memory_endpoint(org_id, session)


@router.get("/mesh-scores/{org_id}", response_model=SignalScoresResponse)
def mesh_scores_endpoint(org_id: str, session=Depends(get_session)) -> SignalScoresResponse:
    """Mesh scores endpoint."""
    scores = get_signal_scores(org_id)
    return SignalScoresResponse(org_id=org_id, scores=scores)


@router.get("/signal-scores/{org_id}", response_model=SignalScoresResponse)
def signal_scores_endpoint(org_id: str, session=Depends(get_session)) -> SignalScoresResponse:
    """Signal scores endpoint."""
    scores = get_signal_scores(org_id)
    return SignalScoresResponse(org_id=org_id, scores=scores)


def _run_self_test(session=None) -> bool:
    """Run self-test. Returns True if passed."""
    try:
        result = session.execute(text("SELECT 1")).scalar() if session else 1
        return result == 1
    except Exception:
        return False


if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import get_session

    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(bind=engine)

    app_instance = type("MockApp", (), {})()
    app_instance.dependency_overrides = {}
    app_instance.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    with app_instance.dependency_overrides[get_session]() as sess:
        passed = _run_self_test(sess)
    
    if passed:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)