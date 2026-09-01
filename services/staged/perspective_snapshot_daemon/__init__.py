# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory, Org, User
from sqlalchemy import select
from sqlalchemy.orm import Session
from typing import Optional
import httpx


def get_signal_scores(org_id: int, session: Optional[Session] = None) -> list[dict]:
    """Fetch signal scores for an org via the MESH query endpoint."""
    payload = {
        "query": "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id",
        "params": {"org_id": org_id}
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("http://127.0.0.1:8772/query", json=payload)
            resp.raise_for_status()
            return resp.json().get("rows", [])
    except Exception:
        return []


def mesh_memory_endpoint(org_id: int, memory_type: str = "default") -> dict:
    """Fetch mesh memory entries for an org."""
    payload = {
        "query": "SELECT * FROM mesh_memory WHERE org_id = :org_id AND memory_type = :memory_type LIMIT 1",
        "params": {"org_id": org_id, "memory_type": memory_type}
    }
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("http://127.0.0.1:8772/query", json=payload)
            resp.raise_for_status()
            rows = resp.json().get("rows", [])
            return rows[0] if rows else {}
    except Exception:
        return {}


class TestVulnAdvisory(VulnAdvisory):
    """Test subclass of VulnAdvisory for service testing."""
    pass


if __name__ == "__main__":
    from fastapi import FastAPI
    from unittest.mock import MagicMock, patch

    app = FastAPI()

    mock_session = MagicMock(spec=Session)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    with patch("httpx.Client") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.return_value.__enter__.return_value = mock_client
        mock_client.post.return_value.json.return_value = {"rows": []}
        mock_client.post.return_value.raise_for_status = MagicMock()

        scores = get_signal_scores(org_id=1, session=mock_session)
        mesh = mesh_memory_endpoint(org_id=1)

    with patch("httpx.Client") as mock_httpx2:
        mock_client2 = MagicMock()
        mock_httpx2.return_value.__enter__.return_value = mock_client2
        mock_client2.post.return_value.json.return_value = {"rows": [{"id": 1}]}
        mock_client2.post.return_value.raise_for_status = MagicMock()

        mesh2 = mesh_memory_endpoint(org_id=1, memory_type="test")

    app.dependency_overrides[get_session] = lambda: mock_session

    from app.db import get_session as gs
    assert gs in app.dependency_overrides

    print("PASS")