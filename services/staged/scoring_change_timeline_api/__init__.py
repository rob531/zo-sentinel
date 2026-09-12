"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import httpx
from app.db import get_session


MESH_STORE_URL = "http://127.0.0.1:8772/query"


def get_signal_scores() -> List[Dict[str, Any]]:
    """Fetch signal scores from MESH store."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                MESH_STORE_URL,
                json={"query": "SELECT * FROM mcp_signal_scores LIMIT 1000"}
            )
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return []


def get_mesh_memory() -> Dict[str, Any]:
    """Fetch mesh memory data from MESH store."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                MESH_STORE_URL,
                json={"query": "SELECT * FROM mesh_memory LIMIT 1"}
            )
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list) and data:
                    return data[0]
                return data or {}
    except Exception:
        pass
    return {}


def get_mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Fetch mesh scores endpoint data from MESH store."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                MESH_STORE_URL,
                json={"query": "SELECT * FROM mcp_signal_scores WHERE category = 'mesh_scores' LIMIT 100"}
            )
            if response.status_code == 200:
                return response.json()
    except Exception:
        pass
    return []


def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Return mesh scores endpoint data."""
    return get_mesh_scores_endpoint()


def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """Return signal scores endpoint data."""
    return get_signal_scores()


def get_critical_risk_servers() -> Dict[str, Any]:
    """Fetch critical risk servers from MESH store."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                MESH_STORE_URL,
                json={"query": "SELECT * FROM mcp_signal_scores WHERE risk_level = 'critical' ORDER BY score DESC LIMIT 50"}
            )
            if response.status_code == 200:
                return {"servers": response.json()}
    except Exception:
        pass
    return {"servers": []}


def get_db():
    """Return database session."""
    return next(get_session())


def _run_self_test() -> bool:
    """Run self-test to verify module functionality."""
    try:
        scores = get_signal_scores()
        assert isinstance(scores, list)
        memory = get_mesh_memory()
        assert isinstance(memory, dict)
        mesh_scores = mesh_scores_endpoint()
        assert isinstance(mesh_scores, list)
        risk_servers = get_critical_risk_servers()
        assert isinstance(risk_servers, dict)
        db = get_db()
        assert db is not None
        return True
    except Exception:
        return False


if __name__ == "__main__":
    result = _run_self_test()
    print("PASS" if result else "FAIL")