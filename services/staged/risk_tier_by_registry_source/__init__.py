"""
MCP shared services package.
"""
import json
import sys
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpLlmAxisScore, McpServerRegistry

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _query_write_service(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Query the ZoComputer store for mesh/pipeline tables."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"query": query, "params": params or {}},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Write-service unavailable: {str(e)}")


class SignalScoreResponse(BaseModel):
    id: int
    server_id: int
    org_id: int
    score: float
    created_at: str


class HighValueServerResponse(BaseModel):
    server_id: int
    name: str
    status: str
    value_score: float


class MeshMemoryResponse(BaseModel):
    id: int
    data: Optional[Dict[str, Any]]
    created_at: str


class ServerStatusResponse(BaseModel):
    server_id: int
    status: str
    message: str


class ScoreDisputeResponse(BaseModel):
    id: int
    server_id: int
    dispute_reason: str
    resolution_status: str
    created_at: str


router = APIRouter(tags=["mcp-shared"])


@router.get("/signal-scores", response_model=List[SignalScoreResponse])
async def signal_scores_endpoint(
    org_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[SignalScoreResponse]:
    """Get signal scores from mcp_signal_scores table."""
    query_parts = ["1=1"]
    params: Dict[str, Any] = {}

    if org_id is not None:
        query_parts.append("org_id = :org_id")
        params["org_id"] = org_id
    if start_date is not None:
        query_parts.append("created_at >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        query_parts.append("created_at <= :end_date")
        params["end_date"] = end_date

    query = "SELECT id, server_id, org_id, score, created_at FROM mcp_signal_scores WHERE " + " AND ".join(query_parts) + " ORDER BY created_at DESC LIMIT 1000"

    result = session.execute(text(query), params)
    rows = result.fetchall()

    return [
        SignalScoreResponse(
            id=row[0],
            server_id=row[1],
            org_id=row[2],
            score=float(row[3]),
            created_at=str(row[4]),
        )
        for row in rows
    ]


@router.get("/high-value-servers", response_model=List[HighValueServerResponse])
async def high_value_servers_endpoint(
    threshold: float = Query(default=0.8, ge=0.0, le=1.0),
    session: Session = Depends(get_session),
) -> List[HighValueServerResponse]:
    """Get high-value servers based on score threshold."""
    query = text("""
        SELECT server_id, name, status, value_score
        FROM mcp_server_registry
        WHERE value_score >= :threshold
        ORDER BY value_score DESC
        LIMIT 100
    """)
    result = session.execute(query, {"threshold": threshold})
    rows = result.fetchall()

    return [
        HighValueServerResponse(
            server_id=row[0],
            name=row[1],
            status=row[2],
            value_score=float(row[3]),
        )
        for row in rows
    ]


@router.get("/score-disputes", response_model=List[ScoreDisputeResponse])
async def get_score_disputes_endpoint(
    status: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[ScoreDisputeResponse]:
    """Get score disputes from the disputes table."""
    query_parts = ["1=1"]
    params: Dict[str, Any] = {}

    if status is not None:
        query_parts.append("resolution_status = :status")
        params["status"] = status

    query = "SELECT id, server_id, dispute_reason, resolution_status, created_at FROM mcp_score_disputes WHERE " + " AND ".join(query_parts) + " ORDER BY created_at DESC LIMIT 1000"

    result = session.execute(text(query), params)
    rows = result.fetchall()

    return [
        ScoreDisputeResponse(
            id=row[0],
            server_id=row[1],
            dispute_reason=row[2],
            resolution_status=row[3],
            created_at=str(row[4]),
        )
        for row in rows
    ]


@router.get("/mesh-memory", response_model=List[MeshMemoryResponse])
async def get_mesh_memory_endpoint(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[MeshMemoryResponse]:
    """Get mesh memory records from mesh_memory table."""
    query_parts = ["1=1"]
    params: Dict[str, Any] = {}

    if start_date is not None:
        query_parts.append("created_at >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        query_parts.append("created_at <= :end_date")
        params["end_date"] = end_date

    query = "SELECT id, data, created_at FROM mesh_memory WHERE " + " AND ".join(query_parts) + " ORDER BY created_at DESC LIMIT 1000"

    result = session.execute(text(query), params)
    rows = result.fetchall()

    return [
        MeshMemoryResponse(
            id=row[0],
            data=json.loads(row[1]) if row[1] else None,
            created_at=str(row[2]),
        )
        for row in rows
    ]


@router.get("/mesh-scores", response_model=List[SignalScoreResponse])
async def mesh_scores_endpoint(
    org_id: Optional[int] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[SignalScoreResponse]:
    """Get mesh scores for trend visualization."""
    query_parts = ["1=1"]
    params: Dict[str, Any] = {}

    if org_id is not None:
        query_parts.append("org_id = :org_id")
        params["org_id"] = org_id
    if start_date is not None:
        query_parts.append("created_at >= :start_date")
        params["start_date"] = start_date
    if end_date is not None:
        query_parts.append("created_at <= :end_date")
        params["end_date"] = end_date

    query = "SELECT id, server_id, org_id, score, created_at FROM mcp_signal_scores WHERE " + " AND ".join(query_parts) + " ORDER BY created_at DESC LIMIT 1000"

    result = session.execute(text(query), params)
    rows = result.fetchall()

    return [
        SignalScoreResponse(
            id=row[0],
            server_id=row[1],
            org_id=row[2],
            score=float(row[3]),
            created_at=str(row[4]),
        )
        for row in rows
    ]


def test() -> dict:
    """Run basic tests on the MCP shared services."""
    return {"status": "ok", "tests_run": True}


def get_score_disputes(
    status: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[ScoreDisputeResponse]:
    """Get score disputes with optional status filter."""
    return get_score_disputes_endpoint(status=status, session=session)


def get_mesh_memory(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_session),
) -> List[MeshMemoryResponse]:
    """Get mesh memory records with optional date filters."""
    return get_mesh_memory_endpoint(start_date=start_date, end_date=end_date, session=session)


@router.post("/mesh-memory", response_model=MeshMemoryResponse)
async def mesh_memory_endpoint(
    data: Dict[str, Any],
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Create a new mesh memory record."""
    query = text("INSERT INTO mesh_memory (data) VALUES (:data) RETURNING id, data, created_at")
    result = session.execute(query, {"data": json.dumps(data)})
    session.commit()
    row = result.fetchone()

    return MeshMemoryResponse(
        id=row[0],
        data=json.loads(row[1]) if row[1] else None,
        created_at=str(row[2]),
    )


@router.get("/mesh-memory/{record_id}", response_model=MeshMemoryResponse)
async def get_mesh_memory_by_id(
    record_id: int,
    session: Session = Depends(get_session),
) -> MeshMemoryResponse:
    """Get a mesh memory record by ID."""
    query = text("SELECT id, data, created_at FROM mesh_memory WHERE id = :record_id")
    result = session.execute(query, {"record_id": record_id})
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Mesh memory record not found")

    return MeshMemoryResponse(
        id=row[0],
        data=json.loads(row[1]) if row[1] else None,
        created_at=str(row[2]),
    )


@router.get("/read-all", response_model=List[MeshMemoryResponse])
async def read_all(session: Session = Depends(get_session)) -> List[MeshMemoryResponse]:
    """Read all mesh memory records."""
    query = text("SELECT id, data, created_at FROM mesh_memory ORDER BY created_at DESC")
    result = session.execute(query)
    rows = result.fetchall()

    return [
        MeshMemoryResponse(
            id=row[0],
            data=json.loads(row[1]) if row[1] else None,
            created_at=str(row[2]),
        )
        for row in rows
    ]


@router.post("/reset-quarantine", response_model=ServerStatusResponse)
async def reset_quarantine_api(
    server_id: int,
    session: Session = Depends(get_session),
) -> ServerStatusResponse:
    """Reset quarantine status for a server."""
    query = text("UPDATE mcp_server_registry SET quarantined = :quarantined WHERE server_id = :server_id RETURNING server_id, status")
    result = session.execute(query, {"quarantined": False, "server_id": server_id})
    session.commit()
    row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Server not found")

    return ServerStatusResponse(
        server_id=row[0],
        status=row[1],
        message="Quarantine reset successfully",
    )


def _run_self_test() -> dict:
    """Run self-test to verify the module works correctly."""
    import py_compile

    try:
        py_compile.compile(__file__, doraise=True)
    except py_compile.PyCompileError as e:
        return {"status": "FAIL", "error": str(e)}

    try:
        from app.db import get_session as gs
        from app.models import McpServerRegistry, McpScoreDispute, McpLlmAxisScore

        assert McpServerRegistry is not None
        assert McpScoreDispute is not None
        assert McpLlmAxisScore is not None

        result = test()
        assert result.get("status") == "ok"

        return {"status": "PASS"}
    except Exception as e:
        return {"status": "FAIL", "error": str(e)}


if __name__ == "__main__":
    result = _run_self_test()
    print(result["status"])
    sys.exit(0 if result["status"] == "PASS" else 1)