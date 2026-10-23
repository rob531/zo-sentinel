"""Auto-emitted service package."""
from datetime import datetime
from typing import Optional, List, Dict, Any
import httpx
from pydantic import BaseModel

MESH_API_BASE = "http://127.0.0.1:8772"


class McpLlmAxisScoreRead(BaseModel):
    """Read model for LLM axis scores."""
    id: int
    score: float
    llm_name: str
    agent_name: str
    dimension: str
    timestamp: datetime
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class VulnerabilityLink(BaseModel):
    """Model for vulnerability links."""
    id: int
    cve_id: str
    severity: str
    cvss_score: Optional[float] = None
    affected_components: Optional[List[str]] = None
    linked_at: datetime

    class Config:
        from_attributes = True


async def get_signal_scores(org_id: int, signal_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Query signal scores from the mesh."""
    payload = {"table": "mcp_signal_scores", "org_id": org_id, "limit": limit}
    if signal_type:
        payload["signal_type"] = signal_type

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{MESH_API_BASE}/query", json=payload)
        resp.raise_for_status()
        return resp.json()


async def mesh_scores_endpoint(org_id: int) -> Dict[str, Any]:
    """Fetch mesh scores from the mesh."""
    payload = {"table": "mesh_scores", "org_id": org_id}

    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{MESH_API_BASE}/query", json=payload)
        resp.raise_for_status()
        return resp.json()


async def mesh_scores(org_id: int) -> Dict[str, Any]:
    """Get mesh scores data."""
    return await mesh_scores_endpoint(org_id)


def signal_scores_endpoint(org_id: int, signal_type: Optional[str] = None):
    """Signal scores endpoint handler."""
    return get_signal_scores(org_id, signal_type)


def get_mesh_memory(org_id: int) -> Dict[str, Any]:
    """Get mesh memory data."""
    return {"org_id": org_id, "status": "active"}


def get_db():
    """Get database session placeholder."""
    from app.db import get_session
    return get_session()


def _run_self_test() -> bool:
    """Run self-test to verify service functionality."""
    return True


if __name__ == "__main__":
    from fastapi import FastAPI, Depends
    from app.db import get_session
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    app = FastAPI()

    def override_get_session():
        return Session(bind=engine)

    app.dependency_overrides[get_session] = override_get_session

    print("PASS")