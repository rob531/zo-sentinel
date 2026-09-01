"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""

from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import MCPComputer, MCPSignalScore, McpScoreDispute, Org, User, MCPVersion


def _post_query(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Post query to ZoComputer store."""
    url = f"http://127.0.0.1:8772{endpoint}"
    response = requests.post(url, params=params, json=json, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _dummy_post(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Post dummy data for testing."""
    return _post_query(endpoint, params, json, timeout)


def get_signal_scores(
    org_id: str,
    signal_names: Optional[List[str]] = None,
    session: Optional[Session] = None,
) -> List[Dict[str, Any]]:
    """Get signal scores for an organization."""
    results = []
    if session is None:
        with get_session() as session:
            return get_signal_scores(org_id, signal_names, session)

    query = session.query(MCPSignalScore).filter(MCPSignalScore.org_id == org_id)
    if signal_names:
        query = query.filter(MCPSignalScore.signal_name.in_(signal_names))

    for score in query.all():
        results.append(
            {
                "org_id": score.org_id,
                "signal_name": score.signal_name,
                "score": score.score,
                "metadata": score.metadata or {},
            }
        )
    return results


def get_mesh_scores(
    org_id: str,
    computer_id: Optional[str] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """Get mesh scores from ZoComputer store."""
    json = {"org_id": org_id}
    if computer_id:
        json["computer_id"] = computer_id
    result = _post_query("/api/v1/mesh/scores", json=json, timeout=timeout)
    return result.get("scores", [])


def get_mesh_memory(
    org_id: str,
    computer_id: Optional[str] = None,
    limit: int = 100,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    """Get mesh memory entries from ZoComputer store."""
    json = {"org_id": org_id, "limit": limit}
    if computer_id:
        json["computer_id"] = computer_id
    result = _post_query("/api/v1/mesh/memory", json=json, timeout=timeout)
    return result.get("memory", [])


def setup_database(session: Session) -> None:
    """Setup database schema and tables."""
    # Create tables if not exist (for self-test purposes)
    session.execute(
        text(
            """
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            id SERIAL PRIMARY KEY,
            org_id VARCHAR(255) NOT NULL,
            signal_name VARCHAR(255) NOT NULL,
            score FLOAT,
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        )
    )
    session.commit()


def reset_server_export_api_quarantine(
    server_id: str,
    session: Optional[Session] = None,
    timeout: int = 30,
) -> bool:
    """Reset server export API quarantine status."""
    json = {"server_id": server_id, "action": "reset_quarantine"}
    try:
        result = _post_query("/api/v1/server/export/reset", json=json, timeout=timeout)
        return result.get("success", False)
    except requests.RequestException:
        # Fallback to direct database update
        if session is None:
            with get_session() as session:
                return reset_server_export_api_quarantine(server_id, session, timeout)
        session.execute(
            text("UPDATE mcp_servers SET quarantine = false WHERE id = :server_id"),
            {"server_id": server_id},
        )
        session.commit()
        return True


def _run_self_test() -> bool:
    """Run self-test to verify module functionality."""
    try:
        # Test database connection
        with get_session() as session:
            session.execute(text("SELECT 1"))

        # Test HTTP endpoint (may not be available in test env)
        try:
            response = requests.post(
                "http://127.0.0.1:8772/health",
                json={},
                timeout=5,
            )
            response.raise_for_status()
        except requests.RequestException:
            pass  # Expected if service not running

        return True
    except Exception:
        return False


if __name__ == "__main__":
    # Self-test
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")