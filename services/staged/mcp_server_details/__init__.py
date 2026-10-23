# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from typing import Optional
from functools import lru_cache
from datetime import datetime
import hashlib
import json

# Security: B113 - requests without timeout fix
DEFAULT_TIMEOUT = 30  # seconds


@lru_cache(maxsize=128)
def get_mesh_memory_endpoint(
    mesh_id: str,
    session=None
) -> Optional[dict]:
    """Fetch mesh memory fromZoComputer store via write_service."""
    if not mesh_id:
        return None
    endpoint = f"http://127.0.0.1:8772/query"
    payload = {
        "table": "mesh_memory",
        "filters": {"mesh_id": mesh_id},
        "columns": ["mesh_id", "data", "updated_at"]
    }
    # Security: B113 fix - timeout applied
    import requests
    try:
        resp = requests.post(endpoint, json=payload, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        return result.get("data", [{}])[0] if result.get("data") else None
    except requests.RequestException:
        return None


def mesh_memory_endpoint(mesh_id: str, session=None) -> dict:
    """Public mesh memory endpoint with error handling."""
    result = get_mesh_memory_endpoint(mesh_id, session)
    if result is None:
        return {"error": "Mesh memory not found", "mesh_id": mesh_id}
    return result


@lru_cache(maxsize=128)
def get_score_disputes_endpoint(
    org_id: str,
    status: Optional[str] = None,
    session=None
) -> list:
    """Fetch score disputes from app db with parameterized query (B608 fix)."""
    from app.db import get_session
    from app.models import McpScoreDispute

    db_session = session or get_session()
    query = db_session.query(McpScoreDispute).filter(
        McpScoreDispute.org_id == org_id
    )
    if status:
        query = query.filter(McpScoreDispute.status == status)
    # Security: B608 fix - using ORM (parameterized) instead of raw SQL
    return query.order_by(McpScoreDispute.created_at.desc()).limit(100).all()


def get_score_disputes(org_id: str, status: Optional[str] = None, session=None) -> list:
    """Public score disputes getter."""
    return get_score_disputes_endpoint(org_id, status, session)


@lru_cache(maxsize=256)
def signal_scores_endpoint(
    mesh_id: str,
    signal_type: Optional[str] = None,
    session=None
) -> dict:
    """Fetch signal scores fromZoComputer store."""
    from app.db import get_session
    from app.models import McpLlmAxisScore

    db_session = session or get_session()
    query = db_session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.mesh_id == mesh_id
    )
    if signal_type:
        query = query.filter(McpLlmAxisScore.signal_type == signal_type)
    scores = query.all()
    return {
        "mesh_id": mesh_id,
        "scores": [
            {"id": s.id, "signal_type": s.signal_type, "score": s.score}
            for s in scores
        ],
        "count": len(scores)
    }


def mesh_scores_endpoint(mesh_id: str, session=None) -> dict:
    """Public mesh scores endpoint."""
    return signal_scores_endpoint(mesh_id, None, session)


def get_mesh_memory(mesh_id: str, session=None) -> Optional[dict]:
    """Alias for get_mesh_memory_endpoint."""
    return get_mesh_memory_endpoint(mesh_id, session)


def compute_mesh_hash(mesh_id: str, salt: str = "") -> str:
    """Compute deterministic hash for mesh_id."""
    data = f"{mesh_id}:{salt}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()[:16]


def serialize_response(data: dict, indent: int = 2) -> str:
    """Serialize response with consistent formatting."""
    return json.dumps(data, indent=indent, default=str)


# Router factory for staged services
def create_router(prefix: str = "", tags: Optional[list] = None):
    """Create FastAPI router with common configuration."""
    from fastapi import APIRouter
    router = APIRouter(prefix=prefix, tags=tags or [])
    return router


# Contract validation helpers
class ContractValidator:
    """Validate data against expected schemas."""

    @staticmethod
    def validate_mesh_memory(data: dict) -> bool:
        required = ["mesh_id", "data"]
        return all(k in data for k in required)

    @staticmethod
    def validate_score_dispute(data: dict) -> bool:
        required = ["org_id", "dispute_id", "status"]
        return all(k in data for k in required)

    @staticmethod
    def validate_signal_score(data: dict) -> bool:
        required = ["mesh_id", "signal_type", "score"]
        return all(k in data for k in required) and isinstance(data["score"], (int, float))


# Registry for service discovery
SERVICE_REGISTRY: dict = {
    "mesh_memory_endpoint": mesh_memory_endpoint,
    "signal_scores_endpoint": signal_scores_endpoint,
    "get_mesh_memory_endpoint": get_mesh_memory_endpoint,
    "get_score_disputes_endpoint": get_score_disputes_endpoint,
    "mesh_scores_endpoint": mesh_scores_endpoint,
    "get_mesh_memory": get_mesh_memory,
    "get_score_disputes": get_score_disputes,
}


def get_service(name: str):
    """Retrieve service function by name."""
    return SERVICE_REGISTRY.get(name)


def list_services() -> list:
    """List all registered services."""
    return list(SERVICE_REGISTRY.keys())


def health_check() -> dict:
    """Basic health check for the package."""
    return {
        "status": "healthy",
        "services": len(SERVICE_REGISTRY),
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    # Self-test
    import sys

    try:
        # Test mesh_hash
        h1 = compute_mesh_hash("test-mesh-123")
        h2 = compute_mesh_hash("test-mesh-123")
        assert h1 == h2, "Hash consistency failed"
        assert len(h1) == 16, "Hash length incorrect"

        # Test serializer
        result = serialize_response({"key": "value", "num": 42})
        assert '"key"' in result and '"value"' in result

        # Test contract validator
        assert ContractValidator.validate_mesh_memory({"mesh_id": "1", "data": {}})
        assert not ContractValidator.validate_mesh_memory({"mesh_id": "1"})

        # Test service registry
        assert get_service("mesh_memory_endpoint") is mesh_memory_endpoint
        assert "signal_scores_endpoint" in list_services()

        # Test health check
        hc = health_check()
        assert hc["status"] == "healthy"
        assert hc["services"] == len(SERVICE_REGISTRY)

        print("PASS")
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)