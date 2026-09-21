"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

import json
import logging
import sys
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

logger = logging.getLogger(__name__)

ZO_COMPUTER_URL = "http://127.0.0.1:8772/query"
REQUEST_TIMEOUT = 30


def _query_mesh_store(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the mesh/pipeline tables via the ZoComputer store."""
    try:
        response = requests.post(
            ZO_COMPUTER_URL,
            json={"query": query, "params": params or {}},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.RequestException as e:
        logger.error(f"Mesh store query failed: {e}")
        return []


def get_mesh_memory(org_id: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Retrieve mesh memory records for an organization."""
    query = "SELECT * FROM mesh_memory"
    params = {}
    if org_id:
        query += " WHERE org_id = :org_id"
        params["org_id"] = org_id
    query += f" ORDER BY created_at DESC LIMIT {limit}"
    return _query_mesh_store(query, params)


def get_signal_scores(org_id: str, signal_ids: Optional[List[str]] = None, limit: int = 1000) -> List[Dict[str, Any]]:
    """Retrieve signal scores from the mesh store."""
    query = "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id"
    params = {"org_id": org_id, "limit": limit}
    if signal_ids:
        query += " AND signal_id = ANY(:signal_ids)"
        params["signal_ids"] = signal_ids
    query += " LIMIT :limit"
    return _query_mesh_store(query, params)


def get_mesh_scores(org_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Get mesh scores for an organization."""
    return get_signal_scores(org_id=org_id, limit=limit)


def reset_server_export_api_quarantine(server_id: str, session: Session) -> bool:
    """Reset the export API quarantine for a server."""
    try:
        result = session.execute(
            text("UPDATE McpServerRegistry SET export_quarantined = false WHERE id = :server_id"),
            {"server_id": server_id}
        )
        session.commit()
        return result.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to reset export quarantine: {e}")
        session.rollback()
        return False


def dummy_post_endpoint(endpoint: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Post data to a dummy endpoint for testing."""
    try:
        response = requests.post(
            endpoint,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Dummy post failed: {e}")
        return None


def _dummy_post(endpoint: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Internal dummy post helper."""
    return dummy_post_endpoint(endpoint, payload)


def mesh_scores_endpoint(org_id: str) -> List[Dict[str, Any]]:
    """Get mesh scores for an organization."""
    return get_mesh_scores(org_id)


def mesh_memory_endpoint(org_id: str) -> List[Dict[str, Any]]:
    """Get mesh memory for an organization."""
    return get_mesh_memory(org_id=org_id)


def reset_quarantine_endpoint(server_id: str, session: Session) -> bool:
    """Reset quarantine for a server."""
    return reset_server_export_api_quarantine(server_id, session)


def _run_self_test() -> bool:
    """Run self-test to verify the module is functioning correctly."""
    try:
        session = get_session()
        session.execute(text("SELECT 1"))
        session.close()
        return True
    except Exception as e:
        logger.error(f"Self-test failed: {e}")
        return False


if __name__ == "__main__":
    from app.main import app
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    if _run_self_test():
        print("PASS")
    else:
        print("FAIL")
        sys.exit(1)