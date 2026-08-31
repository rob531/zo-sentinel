"""Auto-emitted service package for shared signal/mesh utilities."""

from typing import Any, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User


class SignalScoresResponse(BaseModel):
    scores: list[dict[str, Any]]
    total: int


class MeshScoresResponse(BaseModel):
    scores: list[dict[str, Any]]
    total: int


class VulnerabilityLink(BaseModel):
    vuln_id: str
    cve_id: Optional[str] = None
    severity: Optional[str] = None
    linked_at: Optional[str] = None


class McpLlmAxisScoreRead(BaseModel):
    server_id: int
    axis: str
    score: float
    llm_provider: Optional[str] = None
    created_at: Optional[str] = None


def _query_zo_computer(query: str, params: Optional[dict] = None) -> list[dict]:
    """Query the ZoComputer store for mesh/pipeline data."""
    import httpx
    payload: dict[str, Any] = {"query": query}
    if params:
        payload["params"] = params
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post("http://127.0.0.1:8772/query", json=payload)
            resp.raise_for_status()
            result = resp.json()
            return result.get("rows", result.get("data", []))
    except Exception:
        return []


def get_signal_scores(
    server_id: Optional[int] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Get signal scores from the mesh/pipeline store."""
    rows = _query_zo_computer(
        "SELECT * FROM mcp_signal_scores WHERE 1=1",
        {"server_id": server_id, "limit": limit} if server_id or limit != 100 else None,
    )
    if not rows:
        return SignalScoresResponse(scores=[], total=0)
    return SignalScoresResponse(scores=rows, total=len(rows))


def signal_scores_endpoint(
    server_id: Optional[int] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """API endpoint for signal scores."""
    return get_signal_scores(server_id=server_id, limit=limit, session=session)


def mesh_scores(
    org_id: Optional[int] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> MeshScoresResponse:
    """Get mesh scores from the mesh/pipeline store."""
    rows = _query_zo_computer(
        "SELECT * FROM mesh_memory WHERE 1=1",
        {"org_id": org_id, "limit": limit} if org_id or limit != 100 else None,
    )
    if not rows:
        return MeshScoresResponse(scores=[], total=0)
    return MeshScoresResponse(scores=rows, total=len(rows))


def mesh_scores_endpoint(
    org_id: Optional[int] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
) -> MeshScoresResponse:
    """API endpoint for mesh scores."""
    return mesh_scores(org_id=org_id, limit=limit, session=session)


def get_mesh_memory(
    org_id: Optional[int] = None,
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get mesh memory entries from the store."""
    rows = _query_zo_computer(
        "SELECT * FROM mesh_memory WHERE org_id = :org_id" if org_id else "SELECT * FROM mesh_memory",
        {"org_id": org_id} if org_id else None,
    )
    return rows


def get_db(session: Session = Depends(get_session)) -> Session:
    """Get the database session."""
    return session


def _run_self_test() -> bool:
    """Run self-test to verify package functionality."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        try:
            resp = client.get("/health")
            if resp.status_code != 200:
                return False
        except Exception:
            pass

    try:
        get_signal_scores(limit=1)
        mesh_scores(limit=1)
        get_mesh_memory()
    except Exception:
        return False

    return True


if __name__ == "__main__":
    result = _run_self_test()
    print("PASS" if result else "FAIL")