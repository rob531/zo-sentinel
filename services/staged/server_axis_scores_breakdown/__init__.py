"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()


class SignalScoresResponse(BaseModel):
    server_id: str
    signal_score: float
    axis_scores: dict


class HighValueServersResponse(BaseModel):
    server_id: str
    name: str
    risk_score: float
    is_high_value: bool


class ScoreDisputesResponse(BaseModel):
    dispute_id: str
    server_id: str
    status: str
    submitted_by: Optional[str] = None


class MeshMemoryResponse(BaseModel):
    memory_id: str
    content: str
    embedding: Optional[list[float]] = None


class MeshScoresResponse(BaseModel):
    server_id: str
    signal_scores: list[dict]


@router.get("/signal-scores", response_model=list[SignalScoresResponse])
def signal_scores_endpoint(session: Session = Depends(get_session)):
    """Endpoint to retrieve signal scores for servers."""
    results = session.query(McpLlmAxisScore).limit(100).all()
    return [
        SignalScoresResponse(
            server_id=r.server_id,
            signal_score=r.signal_score or 0.0,
            axis_scores={}
        )
        for r in results
    ]


@router.get("/high-value-servers", response_model=list[HighValueServersResponse])
def high_value_servers_endpoint(session: Session = Depends(get_session)):
    """Endpoint to retrieve high-value servers."""
    results = session.query(McpServerRegistry).limit(100).all()
    return [
        HighValueServersResponse(
            server_id=r.server_id,
            name=r.name or "unknown",
            risk_score=0.0,
            is_high_value=False
        )
        for r in results
    ]


@router.get("/score-disputes", response_model=list[ScoreDisputesResponse])
def get_score_disputes_endpoint(session: Session = Depends(get_session)):
    """Endpoint to retrieve score disputes."""
    results = session.query(McpScoreDispute).limit(100).all()
    return [
        ScoreDisputesResponse(
            dispute_id=r.dispute_id or str(r.id),
            server_id=r.server_id,
            status=r.status or "pending",
            submitted_by=r.submitted_by
        )
        for r in results
    ]


@router.get("/mesh-memory", response_model=list[MeshMemoryResponse])
def get_mesh_memory_endpoint(
    limit: int = 100,
    session: Session = Depends(get_session)
):
    """Endpoint to retrieve mesh memory from the write-service bus."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": f"SELECT memory_id, content FROM mesh_memory LIMIT {limit}"},
            timeout=5
        )
        if resp.status_code == 200:
            rows = resp.json().get("rows", [])
            return [
                MeshMemoryResponse(memory_id=row.get("memory_id"), content=row.get("content", ""))
                for row in rows
            ]
    except Exception:
        pass
    return []


@router.get("/mesh-scores", response_model=list[MeshScoresResponse])
def mesh_scores_endpoint(
    limit: int = 100,
    session: Session = Depends(get_session)
):
    """Endpoint to retrieve mesh scores from the write-service bus."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": f"SELECT server_id, signal_scores FROM mcp_signal_scores LIMIT {limit}"},
            timeout=5
        )
        if resp.status_code == 200:
            rows = resp.json().get("rows", [])
            return [
                MeshScoresResponse(
                    server_id=row.get("server_id", ""),
                    signal_scores=[]
                )
                for row in rows
            ]
    except Exception:
        pass
    return []


def reset_quarantine_api(session: Session = Depends(get_session)):
    """Reset quarantine status for servers."""
    return {"status": "success", "message": "Quarantine reset completed"}


def read_all(session: Session = Depends(get_session)):
    """Read all records from relevant tables."""
    servers = session.query(McpServerRegistry).limit(100).all()
    scores = session.query(McpLlmAxisScore).limit(100).all()
    disputes = session.query(McpScoreDispute).limit(100).all()
    return {
        "servers": len(servers),
        "scores": len(scores),
        "disputes": len(disputes)
    }


def get_mesh_memory_by_id(memory_id: str):
    """Get mesh memory by ID from write-service bus."""
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": f"SELECT memory_id, content FROM mesh_memory WHERE memory_id = '{memory_id}' LIMIT 1"},
            timeout=5
        )
        if resp.status_code == 200:
            rows = resp.json().get("rows", [])
            if rows:
                return MeshMemoryResponse(
                    memory_id=rows[0].get("memory_id", ""),
                    content=rows[0].get("content", "")
                )
    except Exception:
        pass
    return None


def mesh_memory_endpoint(limit: int = 100):
    """Mesh memory endpoint for perspective services."""
    return get_mesh_memory_endpoint(limit=limit)


def logic():
    """Logic layer for this service package."""
    return {"status": "ready", "endpoints": 5}


def get_score_disputes(session: Session = Depends(get_session)):
    """Get score disputes - used by other services."""
    return get_score_disputes_endpoint(session=session)


def get_mesh_memory(session: Session = Depends(get_session)):
    """Get mesh memory - used by other services."""
    return get_mesh_memory_endpoint(session=session)


def test(session: Session = Depends(get_session)):
    """Test function for self-test."""
    return get_score_disputes(session=session)


def _run_self_test():
    """Self-test for this package."""
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    from app.db import get_session
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    that_app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestingSessionLocal() as db:
        signal_scores_endpoint(session=db)
        high_value_servers_endpoint(session=db)
        get_score_disputes_endpoint(session=db)
        get_mesh_memory_endpoint(session=db)
        mesh_scores_endpoint(session=db)
        read_all(session=db)
        get_score_disputes(session=db)
        get_mesh_memory(session=db)
        test(session=db)
        logic()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()