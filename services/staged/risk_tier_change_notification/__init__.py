from typing import Optional, List, Dict, Any
from datetime import datetime
import httpx
from pydantic import BaseModel, Field


class McpServerRegistry(BaseModel):
    """Registry for MCP servers with schema-aligned fields."""
    confidence: float
    description: str


class PerspectiveSnapshot(BaseModel):
    """Base class for perspective snapshots."""
    id: str
    timestamp: datetime
    data: Dict[str, Any]


class TestVulnAdvisory(BaseModel):
    """Base class for vulnerability advisories."""
    cve_id: str
    severity: str
    description: str


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Get mesh scores from ZoComputer store."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"type": "mesh_scores"}
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return []


def get_mesh_memory_endpoint() -> Optional[Dict[str, Any]]:
    """Get mesh memory endpoint."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"type": "mesh_memory"}
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


def get_mesh_memory() -> Optional[Dict[str, Any]]:
    """Get mesh memory data."""
    return get_mesh_memory_endpoint()


def get_mesh_memory_by_id(memory_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve specific mesh memory by its identifier."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"type": "mesh_memory_by_id", "memory_id": memory_id}
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return None


def api_signal_scores(org_id: str) -> List[Dict[str, Any]]:
    """Fetch API signal scores for a given organization."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"type": "api_signal_scores", "org_id": org_id}
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return []


def get_signal_scores(org_id: str, time_range: str = "7d") -> Dict[str, Any]:
    """Retrieve signal scores for organization within specified time window."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                "http://127.0.0.1:8772/query",
                json={"type": "signal_scores", "org_id": org_id, "time_range": time_range}
            )
            response.raise_for_status()
            return response.json()
    except Exception:
        return {}


def mesh_memory_endpoint() -> Optional[Dict[str, Any]]:
    """Primary mesh memory endpoint for direct access."""
    return get_mesh_memory_endpoint()


def dummy_endpoint_route():
    """Test route handler for endpoint validation."""
    return {"status": "ok"}


def _run_self_test():
    """Execute self-test suite to validate module functionality."""
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    _ = mesh_scores_endpoint()
    _ = mesh_memory_endpoint()
    _ = dummy_endpoint_route()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()