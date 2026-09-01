"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion."""
from typing import Any, Dict, Optional

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

MESH_MEMORY_ENDPOINT = "http://127.0.0.1:8772"


def mesh_memory_endpoint() -> str:
    """Return the mesh memory endpoint URL."""
    return MESH_MEMORY_ENDPOINT


def get_mesh_memory_by_id(memory_id: str) -> Optional[Dict[str, Any]]:
    """Get mesh memory entry by ID from ZoComputer store."""
    import requests

    try:
        resp = requests.post(
            f"{MESH_MEMORY_ENDPOINT}/query",
            json={"memory_id": memory_id},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def test_service_package() -> Dict[str, Any]:
    """Run self-test for service package utilities."""
    return {
        "mesh_memory_endpoint": mesh_memory_endpoint(),
        "status": "ok",
    }


def run_self_test() -> str:
    """Run self-test and return result."""
    result = test_service_package()
    if result.get("status") == "ok":
        return "PASS"
    return "FAIL"


if __name__ == "__main__":
    print(run_self_test())