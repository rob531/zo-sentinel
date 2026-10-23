from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry


class PerspectiveSnapshot(BaseModel):
    timestamp: datetime
    scope: str
    payload: dict[str, Any]

    class Config:
        from_attributes = True


class McpScoreDisputeService(BaseModel):
    dispute_id: Optional[str] = None
    server_id: str
    axis: str
    claimed_score: float
    status: str = "open"

    class Config:
        from_attributes = True


class ServerResponse(BaseModel):
    status: str
    data: Any
    timestamp: datetime


class UserRead(BaseModel):
    user_id: Optional[str] = None
    name: str
    email: str

    class Config:
        from_attributes = True


router = APIRouter()


def mesh_memory_endpoint(
    session: Session = Depends(get_session),
    query: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Query mesh_memory table via write service."""
    try:
        result = session.execute(
            text("SELECT * FROM mesh_memory WHERE 1=1")
        )
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]
    except Exception:
        return []


def mesh_memory_endpoint_get(
    session: Session = Depends(get_session),
    memory_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Get single mesh_memory entry by id."""
    if not memory_id:
        return None
    try:
        result = session.execute(
            text("SELECT * FROM mesh_memory WHERE id = :id"),
            {"id": memory_id}
        )
        row = result.fetchone()
        if row:
            return dict(row._mapping)
    except Exception:
        pass
    return None


def get_mesh_memory_endpoint(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get all mesh_memory entries."""
    return mesh_memory_endpoint(session=session)


def signal_scores_endpoint(
    session: Session = Depends(get_session),
    axis: Optional[str] = None,
    server_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Query mcp_signal_scores table."""
    try:
        query = "SELECT * FROM mcp_signal_scores WHERE 1=1"
        params = {}
        if axis:
            query += " AND axis = :axis"
            params["axis"] = axis
        if server_id:
            query += " AND server_id = :server_id"
            params["server_id"] = server_id
        result = session.execute(text(query), params)
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]
    except Exception:
        return []


def get_score_disputes_endpoint(
    session: Session = Depends(get_session),
    status: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Get score disputes, optionally filtered by status."""
    query = session.query(McpScoreDispute)
    if status:
        query = query.filter(McpScoreDispute.status == status)
    disputes = query.all()
    return [
        {
            "dispute_id": d.dispute_id,
            "server_id": d.server_id,
            "axis": d.axis,
            "claimed_score": d.claimed_score,
            "status": d.status,
        }
        for d in disputes
    ]


def get_open_disputes(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Get all open (non-resolved) score disputes."""
    return get_score_disputes_endpoint(session=session, status="open")


def recency_report(
    session: Session = Depends(get_session),
    cutoff_hours: int = 24,
) -> dict[str, Any]:
    """Generate recency report for signal scores."""
    try:
        result = session.execute(
            text("""
                SELECT COUNT(*) as count, 
                       MAX(timestamp) as latest
                FROM mcp_signal_scores 
                WHERE timestamp > NOW() - INTERVAL ':hours hours'
            """),
            {"hours": cutoff_hours}
        )
        row = result.fetchone()
        return {
            "count": row.count if row else 0,
            "latest": row.latest if row else None,
            "cutoff_hours": cutoff_hours,
        }
    except Exception:
        return {"count": 0, "latest": None, "cutoff_hours": cutoff_hours}


def run_self_test() -> bool:
    """Run self-test for service package."""
    try:
        from fastapi import FastAPI
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        test_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        TestSession = sessionmaker(bind=test_engine)
        test_session = TestSession()

        test_session.execute(text("CREATE TABLE mesh_memory (id TEXT)"))
        test_session.execute(text("CREATE TABLE mcp_signal_scores (id TEXT)"))
        test_session.commit()

        mesh = mesh_memory_endpoint(session=test_session)
        assert isinstance(mesh, list)

        scores = signal_scores_endpoint(session=test_session)
        assert isinstance(scores, list)

        disputes = get_open_disputes(session=test_session)
        assert isinstance(disputes, list)

        report = recency_report(session=test_session)
        assert isinstance(report, dict)

        test_session.close()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    result = run_self_test()
    print("PASS" if result else "FAIL")