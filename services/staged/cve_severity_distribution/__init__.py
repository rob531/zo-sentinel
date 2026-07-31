"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite.
"""

import json
from typing import Any, Dict, List, Optional

import requests
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org, User


def _post_query(
    query: str,
    params: Optional[Dict[str, Any]] = None,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Execute a query against the app database."""
    result = session.execute(text(query), params or {})
    rows = result.fetchall()
    return [dict(row._mapping) for row in rows]


def _run_self_test(session: Session = Depends(get_session)) -> str:
    """Run self-test to verify module is operational."""
    try:
        session.execute(text("SELECT 1"))
        return "PASS"
    except Exception as e:
        return f"FAIL: {e}"


def get_mesh_scores(
    org_id: int,
    signal_type: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Retrieve mesh scores for an org from ZoComputer store."""
    payload = {
        "query": "SELECT * FROM mcp_signal_scores WHERE org_id = :org_id",
        "params": {"org_id": org_id},
    }
    if signal_type:
        payload["query"] += " AND signal_type = :signal_type"
        payload["params"]["signal_type"] = signal_type

    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException:
        return []


def get_mesh_memory(
    org_id: int,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Retrieve mesh memory entries for an org from ZoComputer store."""
    payload = {
        "query": "SELECT * FROM mesh_memory WHERE org_id = :org_id",
        "params": {"org_id": org_id},
    }
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except requests.RequestException:
        return []


def mesh_scores_endpoint(
    org_id: int,
    signal_type: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Endpoint-style retrieval of mesh scores."""
    scores = get_mesh_scores(org_id, signal_type, session)
    return {"org_id": org_id, "scores": scores, "count": len(scores)}


def mesh_memory_endpoint(
    org_id: int,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Endpoint-style retrieval of mesh memory."""
    memory = get_mesh_memory(org_id, session)
    return {"org_id": org_id, "memory": memory, "count": len(memory)}


def signal_scores_endpoint(
    org_id: int,
    signal_type: str,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Endpoint for signal-specific scores."""
    return mesh_scores_endpoint(org_id, signal_type, session)


def reset_quarantine_api(
    server_id: int,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Reset quarantine status for an MCP server."""
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.id == server_id
    ).first()
    if server:
        server.quarantined = False
        session.commit()
        return {"server_id": server_id, "status": "reset"}
    return {"server_id": server_id, "status": "not_found"}


def dummy_post_endpoint(
    data: Dict[str, Any],
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Dummy POST endpoint for testing."""
    return {"received": data, "status": "ok"}


def _dummy_post(
    data: Dict[str, Any],
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Internal dummy post for compatibility."""
    return dummy_post_endpoint(data, session)


if __name__ == "__main__":
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        result = _run_self_test(session)
        print(result)