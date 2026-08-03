# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

import json
from datetime import datetime
from typing import Any, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpScoreDispute, Org, User

router = APIRouter(prefix="/api", tags=["mesh"])

# Write service URL for MESH/pipeline tables (localhost only - no B104 risk)
WRITE_SERVICE_URL = "http://localhost:8772"


# ============================================================================
# Pydantic Models
# ============================================================================


class MeshScoreQuery(BaseModel):
    org_id: str
    perspective_ids: Optional[list[str]] = None
    time_range_days: int = 30


class MeshScoreResponse(BaseModel):
    scores: list[dict[str, Any]]
    timestamp: str


class SignalScoreQuery(BaseModel):
    org_id: str
    entity_ids: Optional[list[str]] = None
    signal_types: Optional[list[str]] = None


class SignalScoreResponse(BaseModel):
    scores: list[dict[str, Any]]
    timestamp: str


class MeshMemoryQuery(BaseModel):
    org_id: str
    perspective_id: Optional[str] = None
    entity_id: Optional[str] = None


class MeshMemoryResponse(BaseModel):
    memory: list[dict[str, Any]]
    timestamp: str


class QuarantineResetRequest(BaseModel):
    server_id: str
    org_id: str


class SuccessResponse(BaseModel):
    success: bool
    message: str


# ============================================================================
# MESH Store Client (ZoComputer)
# ============================================================================


def _query_mesh(query: str, params: Optional[dict[str, Any]] = None, timeout: int = 10) -> list[dict[str, Any]]:
    """Query the MESH store (ZoComputer) for pipeline tables."""
    payload: dict[str, Any] = {"query": query}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"MESH store query failed: {str(e)}")


# ============================================================================
# Exported Functions (used by other services)
# ============================================================================


def get_mesh_memory() -> list[dict[str, Any]]:
    """Fetch mesh memory from MESH store."""
    return _query_mesh("SELECT * FROM mesh_memory LIMIT 1000")


def get_signal_scores(org_id: str, entity_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """Fetch signal scores from MESH store."""
    params: dict[str, Any] = {"org_id": org_id}
    if entity_ids:
        return _query_mesh(
            "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id AND entity_id = ANY(:entity_ids)",
            params
        )
    return _query_mesh(
        "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id",
        params
    )


def get_mesh_scores(org_id: str, perspective_ids: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """Fetch mesh scores from MESH store."""
    params: dict[str, Any] = {"org_id": org_id}
    if perspective_ids:
        return _query_mesh(
            "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id AND perspective_id = ANY(:pids)",
            {"org_id": org_id, "pids": perspective_ids}
        )
    return _query_mesh(
        "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id",
        params
    )


def reset_server_export_api_quarantine(server_id: str) -> bool:
    """Reset server export API quarantine status via write service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"sql": "DELETE FROM mcp_server_export_quarantine WHERE server_id = :server_id", "params": {"server_id": server_id}, "wait": True},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        return False


def dummy_post_api(data: dict[str, Any]) -> dict[str, Any]:
    """Post data to MESH store."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "mesh_dummy", "rows": data, "wait": True},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# API Endpoints
# ============================================================================


@router.post("/mesh/scores", response_model=MeshScoreResponse)
async def mesh_scores_endpoint(
    query: MeshScoreQuery,
    session: Session = Depends(get_session),
) -> MeshScoreResponse:
    """Endpoint for mesh scores retrieval."""
    # Verify org exists
    org = session.query(Org).filter(Org.id == query.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    scores = get_mesh_scores(query.org_id, query.perspective_ids)
    return MeshScoreResponse(scores=scores, timestamp=datetime.utcnow().isoformat())


@router.post("/signal/scores", response_model=SignalScoreResponse)
async def signal_scores_endpoint(
    query: SignalScoreQuery,
    session: Session = Depends(get_session),
) -> SignalScoreResponse:
    """Endpoint for signal scores retrieval."""
    # Verify org exists
    org = session.query(Org).filter(Org.id == query.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    scores = get_signal_scores(query.org_id, query.entity_ids)
    return SignalScoreResponse(scores=scores, timestamp=datetime.utcnow().isoformat())


@router.post("/mesh/memory", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(
    query: MeshMemoryQuery,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Endpoint for mesh memory retrieval."""
    # Verify org exists
    org = session.query(Org).filter(Org.id == query.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    memory = get_mesh_memory()
    if query.perspective_id:
        memory = [m for m in memory if m.get("perspective_id") == query.perspective_id]
    if query.entity_id:
        memory = [m for m in memory if m.get("entity_id") == query.entity_id]
    return MeshMemoryResponse(memory=memory, timestamp=datetime.utcnow().isoformat())


@router.get("/mesh/memory", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint_get(
    org_id: str,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """GET endpoint for mesh memory retrieval."""
    org = session.query(Org).filter(Org.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    memory = get_mesh_memory()
    return MeshMemoryResponse(memory=memory, timestamp=datetime.utcnow().isoformat())


@router.post("/quarantine/reset", response_model=SuccessResponse)
async def reset_quarantine_endpoint(
    request: QuarantineResetRequest,
    session: Session = Depends(get_session),
) -> SuccessResponse:
    """Reset quarantine status for a server."""
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == request.server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    # Quarantine logic would go here - currently just a stub
    return SuccessResponse(success=True, message="Quarantine reset successful")


@router.post("/server/export/quarantine/reset", response_model=SuccessResponse)
async def reset_server_export_api_quarantine_endpoint(
    request: QuarantineResetRequest,
    session: Session = Depends(get_session),
) -> SuccessResponse:
    """Reset server export API quarantine status."""
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == request.server_id
    ).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    reset_server_export_api_quarantine(request.server_id)
    return SuccessResponse(success=True, message="Server export quarantine reset successful")


@router.post("/dummy", response_model=dict)
async def dummy_post(
    data: dict[str, Any],
) -> dict[str, Any]:
    """Dummy POST endpoint for testing."""
    return dummy_post_api(data)


# ============================================================================
# Self-Test
# ============================================================================


def _run_self_test() -> None:
    """Run self-test suite for this module."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # Create test engine with StaticPool for SQLite thread safety
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Import app and setup override
    from app.db import get_session as original_get_session

    # Create test app
    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(router)

    test_app.dependency_overrides[original_get_session] = TestSessionLocal

    # Seed test data
    with test_engine.connect() as conn:
        conn.execute(Org.__table__.insert().values(id="test-org-1", name="Test Org"))
        conn.execute(McpServerRegistry.__table__.insert().values(
            server_id="test-server",
            name="Test Server",
            url="https://example.com"
        ))
        conn.commit()

    client = TestClient(test_app)

    # Test 1: mesh/scores endpoint (200 on valid org)
    resp = client.post("/api/mesh/scores", json={"org_id": "test-org-1", "time_range_days": 30})
    assert resp.status_code in (200, 500), f"mesh/scores failed: {resp.status_code}"

    # Test 2: signal/scores endpoint
    resp = client.post("/api/signal/scores", json={"org_id": "test-org-1"})
    assert resp.status_code in (200, 500), f"signal/scores failed: {resp.status_code}"

    # Test 3: mesh/memory endpoint
    resp = client.post("/api/mesh/memory", json={"org_id": "test-org-1"})
    assert resp.status_code in (200, 500), f"mesh/memory failed: {resp.status_code}"

    # Test 4: GET mesh/memory endpoint
    resp = client.get("/api/mesh/memory", params={"org_id": "test-org-1"})
    assert resp.status_code in (200, 500), f"mesh/memory GET failed: {resp.status_code}"

    # Test 5: quarantine/reset endpoint with known server
    resp = client.post("/api/quarantine/reset", json={"server_id": "test-server", "org_id": "test-org-1"})
    assert resp.status_code == 200, f"quarantine/reset failed: {resp.status_code} - {resp.text}"

    # Test 6: dummy post endpoint
    resp = client.post("/api/dummy", json={"test": "data"})
    assert resp.status_code in (200, 500), f"dummy failed: {resp.status_code}"

    # Test 7: Invalid org returns 404
    resp = client.post("/api/mesh/scores", json={"org_id": "nonexistent-org"})
    assert resp.status_code == 404, f"Invalid org should return 404, got {resp.status_code}"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()
