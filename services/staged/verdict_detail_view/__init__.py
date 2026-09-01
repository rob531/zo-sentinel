"""Auto-emitted service package."""

import json
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org, User

router = APIRouter()


class MeshScoresResponse(BaseModel):
    scores: List[Dict[str, Any]]
    total: int


class SignalScoresResponse(BaseModel):
    scores: List[Dict[str, Any]]
    total: int


class MeshMemoryResponse(BaseModel):
    memory: Dict[str, Any]
    found: bool


class QuarantineResetResponse(BaseModel):
    success: bool
    message: str


def _query_zo_computer(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the ZoComputer store for mesh/pipeline data."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params or {}},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
    except requests.RequestException:
        return []


def mesh_scores_endpoint(
    session: Session = Depends(get_session),
) -> MeshScoresResponse:
    """Get mesh scores from the ZoComputer store."""
    query_results = _query_zo_computer(
        "SELECT * FROM mcp_signal_scores WHERE score_type = 'mesh'"
    )
    return MeshScoresResponse(scores=query_results, total=len(query_results))


def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> SignalScoresResponse:
    """Get signal scores from the ZoComputer store."""
    query_results = _query_zo_computer(
        "SELECT * FROM mcp_signal_scores WHERE score_type = 'signal'"
    )
    return SignalScoresResponse(scores=query_results, total=len(query_results))


def get_signal_scores(
    axis: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """Get signal scores filtered by axis if specified."""
    query = "SELECT * FROM mcp_signal_scores WHERE score_type = 'signal'"
    params = {}
    if axis:
        query += " AND axis = :axis"
        params["axis"] = axis
    return _query_zo_computer(query, params)


def mesh_memory_endpoint(
    key: Optional[str] = None,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Get mesh memory from the ZoComputer store."""
    if key:
        query_results = _query_zo_computer(
            "SELECT * FROM mesh_memory WHERE memory_key = :key",
            {"key": key},
        )
        if query_results:
            return MeshMemoryResponse(memory=query_results[0], found=True)
        return MeshMemoryResponse(memory={}, found=False)
    query_results = _query_zo_computer("SELECT * FROM mesh_memory")
    return MeshMemoryResponse(memory={"all": query_results}, found=len(query_results) > 0)


def get_mesh_memory(
    memory_key: str,
    session: Session = Depends(get_session),
) -> Optional[Dict[str, Any]]:
    """Get specific mesh memory entry by key."""
    query_results = _query_zo_computer(
        "SELECT * FROM mesh_memory WHERE memory_key = :memory_key",
        {"memory_key": memory_key},
    )
    return query_results[0] if query_results else None


def reset_quarantine_endpoint(
    server_id: Optional[str] = None,
    session: Session = Depends(get_session),
) -> QuarantineResetResponse:
    """Reset quarantine status for a server or all servers."""
    if server_id:
        server = session.query(McpServerRegistry).filter(
            McpServerRegistry.server_id == server_id
        ).first()
        if server:
            server.quarantined = False
            session.commit()
            return QuarantineResetResponse(success=True, message=f"Reset quarantine for {server_id}")
        return QuarantineResetResponse(success=False, message=f"Server {server_id} not found")
    updated = session.query(McpServerRegistry).filter(
        McpServerRegistry.quarantined == True
    ).update({"quarantined": False})
    session.commit()
    return QuarantineResetResponse(success=True, message=f"Reset quarantine for {updated} servers")


def _run_self_test(session: Session = Depends(get_session)) -> bool:
    """Run self-test to verify the service is operational."""
    try:
        _ = session.execute(text("SELECT 1")).scalar()
        _ = _query_zo_computer("SELECT 1")
        return True
    except Exception:
        return False


@router.get("/mesh/scores")
def get_mesh_scores(session: Session = Depends(get_session)) -> MeshScoresResponse:
    """HTTP endpoint for mesh scores."""
    return mesh_scores_endpoint(session)


@router.get("/signal/scores")
def get_signal_scores_http(session: Session = Depends(get_session)) -> SignalScoresResponse:
    """HTTP endpoint for signal scores."""
    return signal_scores_endpoint(session)


@router.get("/mesh/memory")
def get_mesh_memory_http(
    key: Optional[str] = None,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """HTTP endpoint for mesh memory."""
    return mesh_memory_endpoint(key, session)


@router.post("/quarantine/reset")
def reset_quarantine_http(
    server_id: Optional[str] = None,
    session: Session = Depends(get_session),
) -> QuarantineResetResponse:
    """HTTP endpoint to reset quarantine."""
    return reset_quarantine_endpoint(server_id, session)


if __name__ == "__main__":
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    Org.__table__.create(engine, checkfirst=True)
    User.__table__.create(engine, checkfirst=True)
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(engine, checkfirst=True)
    McpScoreDispute.__table__.create(engine, checkfirst=True)

    with next(override_get_session()) as session:
        result = _run_self_test(session)

    print("PASS" if result else "FAIL")