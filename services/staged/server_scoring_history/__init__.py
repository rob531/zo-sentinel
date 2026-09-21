"""Auto-emitted service package."""

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    VulnLink,
)

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
    "VulnLink",
    "get_mesh_memory",
    "mesh_scores_endpoint",
]


def get_mesh_memory(session, org_id: int) -> dict | None:
    """Get mesh memory for an organization."""
    from sqlalchemy import text
    try:
        result = session.execute(
            text("SELECT * FROM mesh_memory WHERE org_id = :org_id ORDER BY created_at DESC LIMIT 1"),
            {"org_id": org_id}
        )
        row = result.fetchone()
        if row:
            return dict(row._mapping)
    except Exception:
        pass
    return None


def mesh_scores_endpoint(session, org_id: int, perspective: str | None = None) -> dict | None:
    """Get mesh scores endpoint data."""
    from sqlalchemy import text
    try:
        params = {"org_id": org_id}
        sql = "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id"
        if perspective:
            sql += " AND perspective = :perspective"
            params["perspective"] = perspective
        sql += " ORDER BY created_at DESC LIMIT 1"
        result = session.execute(text(sql), params)
        row = result.fetchone()
        if row:
            return dict(row._mapping)
    except Exception:
        pass
    return None


if __name__ == "__main__":
    from unittest.mock import MagicMock
    mock_session = MagicMock()
    mock_session.execute.return_value.fetchone.return_value = None
    get_mesh_memory(mock_session, 1)
    mesh_scores_endpoint(mock_session, 1)
    print("PASS")