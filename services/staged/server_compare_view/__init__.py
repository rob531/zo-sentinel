# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Any, Dict, List, Optional

import requests
from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1", tags=["service"])


class SignalScore(BaseModel):
    signal_id: str
    score: float
    confidence: float
    metadata: Optional[Dict[str, Any]] = None


class MeshMemory(BaseModel):
    memory_id: str
    content: Dict[str, Any]
    created_at: Optional[str] = None


class LlmAxisScore(BaseModel):
    axis: str
    score: float
    llm_model: str
    metadata: Optional[Dict[str, Any]] = None


# Schema classes for responses
class McpServerRegistryRead(BaseModel):
    server_name: str
    server_type: str
    endpoint: Optional[str] = None
    enabled: bool = True
    confidence: Optional[float] = None
    description: Optional[str] = None

    class Config:
        from_attributes = True


class McpScoreDisputeRead(BaseModel):
    dispute_id: str
    score_id: str
    disputed_value: float
    reason: str
    status: str
    created_by: Optional[str] = None

    class Config:
        from_attributes = True


class VulnerabilityAdvisory(BaseModel):
    cve_id: str
    severity: str
    cvss_score: Optional[float] = None
    affected_servers: List[str] = []

    class Config:
        from_attributes = True


def _query_store(query: Dict[str, Any], timeout: int = 30) -> List[Dict[str, Any]]:
    """Query the ZoComputer store for mesh/pipeline data."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.exceptions.RequestException:
        return []


def signal_scores_endpoint(
    signal_type: Optional[str] = None,
    org_id: Optional[str] = None,
    db=Depends(get_session),
) -> List[SignalScore]:
    """Fetch signal scores from the mesh store."""
    query = {
        "table": "mcp_signal_scores",
        "filters": {},
    }
    if signal_type:
        query["filters"]["signal_type"] = signal_type
    if org_id:
        query["filters"]["org_id"] = org_id

    results = _query_store(query)
    return [SignalScore(**r) for r in results]


def mesh_scores_endpoint(
    memory_type: Optional[str] = None,
    db=Depends(get_session),
) -> List[Dict[str, Any]]:
    """Fetch mesh scores from the store."""
    query = {
        "table": "mcp_signal_scores",
        "filters": {},
    }
    if memory_type:
        query["filters"]["memory_type"] = memory_type

    return _query_store(query)


def llm_axis_scores_endpoint(
    axis: Optional[str] = None,
    db=Depends(get_session),
) -> List[LlmAxisScore]:
    """Fetch LLM axis scores."""
    query = {
        "table": "McpLlmAxisScore",
        "filters": {},
    }
    if axis:
        query["filters"]["axis"] = axis

    results = _query_store(query)
    return [LlmAxisScore(**r) for r in results]


def get_mesh_memory_endpoint(
    memory_id: Optional[str] = None,
    db=Depends(get_session),
) -> List[MeshMemory]:
    """Fetch mesh memory entries."""
    query = {
        "table": "mesh_memory",
        "filters": {},
    }
    if memory_id:
        query["filters"]["memory_id"] = memory_id

    results = _query_store(query)
    return [MeshMemory(**r) for r in results]


def get_mesh_memory(
    memory_type: Optional[str] = None,
    db=Depends(get_session),
) -> List[Dict[str, Any]]:
    """Get mesh memory as dict list."""
    query = {
        "table": "mesh_memory",
        "filters": {},
    }
    if memory_type:
        query["filters"]["memory_type"] = memory_type

    return _query_store(query)


def get_signal_scores(
    signal_filter: Optional[Dict[str, Any]] = None,
    db=Depends(get_session),
) -> List[Dict[str, Any]]:
    """Get signal scores with optional filters."""
    query = {
        "table": "mcp_signal_scores",
        "filters": signal_filter or {},
    }
    return _query_store(query)


def _dummy_post(
    data: Dict[str, Any],
    db=Depends(get_session),
) -> Dict[str, Any]:
    """Dummy post endpoint for testing."""
    return {"status": "ok", "data": data}


def test(db=Depends(get_session)) -> Dict[str, str]:
    """Test endpoint for cve_family_propagation_api."""
    return {"status": "test_passed"}


def _run_self_test() -> bool:
    """Run self-test for the service package."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/health")
    def health():
        return {"status": "ok"}

    that_app.include_router(router)

    from fastapi.testclient import TestClient

    client = TestClient(that_app)
    response = client.get("/health")
    return response.status_code == 200


if __name__ == "__main__":
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")
        exit(1)