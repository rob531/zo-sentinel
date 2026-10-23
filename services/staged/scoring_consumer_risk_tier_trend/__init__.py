"""Auto-emitted service package. Relative intra-service imports survive staged->active promotion without rewrite."""

from typing import Any, Dict, List, Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

BUS_HOST = "http://127.0.0.1:8772"

router = APIRouter()


class MeshMemoryRecord(BaseModel):
    id: Optional[int] = None
    key: str
    value: Any
    metadata: Optional[Dict[str, Any]] = None


class MeshMemoryResponse(BaseModel):
    records: List[MeshMemoryRecord]
    count: int


class SignalScoreRecord(BaseModel):
    id: Optional[int] = None
    server_id: str
    axis: str
    score: float
    metadata: Optional[Dict[str, Any]] = None


class SignalScoresResponse(BaseModel):
    records: List[SignalScoreRecord]
    count: int


def mesh_memory_endpoint(query: Optional[str] = None, limit: int = 100) -> MeshMemoryResponse:
    """Query mesh_memory from the ZoComputer store."""
    payload: Dict[str, Any] = {"table": "mesh_memory", "limit": limit}
    if query:
        payload["query"] = query
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{BUS_HOST}/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Bus unavailable: {e}")
    records = [MeshMemoryRecord(**r) for r in data.get("results", [])]
    return MeshMemoryResponse(records=records, count=len(records))


def mesh_memory_endpoint_get(key: str) -> Optional[MeshMemoryRecord]:
    """Get a specific mesh_memory record by key."""
    payload = {"table": "mesh_memory", "query": {"key": key}, "limit": 1}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{BUS_HOST}/query", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Bus unavailable: {e}")
    results = data.get("results", [])
    if not results:
        return None
    return MeshMemoryRecord(**results[0])


def signal_scores_endpoint(
    session: Session, server_id: Optional[str] = None, axis: Optional[str] = None
) -> List[McpLlmAxisScore]:
    """Fetch signal scores from app database, optionally filtered."""
    q = session.query(McpLlmAxisScore)
    if server_id:
        q = q.filter(McpLlmAxisScore.server_id == server_id)
    if axis:
        q = q.filter(McpLlmAxisScore.axis == axis)
    return q.all()


def get_open_disputes(session: Session) -> List[McpScoreDispute]:
    """Fetch open disputes from app database."""
    return session.query(McpScoreDispute).filter(
        McpScoreDispute.status != "resolved"
    ).all()


def get_mesh_memory_endpoint(
    key: Optional[str] = Query(None), query: Optional[str] = Query(None)
) -> MeshMemoryResponse:
    """HTTP endpoint wrapper for mesh_memory queries."""
    if key:
        record = mesh_memory_endpoint_get(key)
        return MeshMemoryResponse(records=[record] if record else [], count=1 if record else 0)
    return mesh_memory_endpoint(query=query)


def get_score_disputes_endpoint(
    session: Session = Depends(get_session), status: str = Query("open")
) -> Dict[str, Any]:
    """HTTP endpoint for score disputes."""
    disputes = session.query(McpScoreDispute).filter(
        McpScoreDispute.status == status
    ).all()
    return {"disputes": [{"id": d.id, "server_id": d.server_id, "axis": d.axis, "status": d.status} for d in disputes]}


@router.get("/mesh-memory", response_model=MeshMemoryResponse)
def api_mesh_memory(
    key: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 100,
):
    return get_mesh_memory_endpoint(key=key, query=query)


@router.get("/signal-scores")
def api_signal_scores(
    session: Session = Depends(get_session),
    server_id: Optional[str] = None,
    axis: Optional[str] = None,
):
    scores = signal_scores_endpoint(session, server_id=server_id, axis=axis)
    return {"scores": [{"id": s.id, "server_id": s.server_id, "axis": s.axis, "score": s.score} for s in scores]}


@router.get("/score-disputes")
def api_score_disputes(
    session: Session = Depends(get_session),
    status: str = "open",
):
    return get_score_disputes_endpoint(session=session, status=status)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()
    test_session.add(McpServerRegistry(id="test-srv", name="Test Server", risk_tier="medium"))
    test_session.add(McpLlmAxisScore(id=1, server_id="test-srv", axis="security", score=0.85))
    test_session.add(McpScoreDispute(id=1, server_id="test-srv", axis="security", status="open"))
    test_session.commit()

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    with httpx.Client(timeout=30.0) as client:
        try:
            resp = client.post(f"{BUS_HOST}/query", json={"table": "mesh_memory", "limit": 1})
            bus_ok = resp.status_code == 200
        except Exception:
            bus_ok = False

    with httpx.Client(timeout=30.0, base_url="http://test") as client:
        from fastapi.testclient import TestClient
        tc = TestClient(test_app)
        scores_resp = tc.get("/signal-scores")
        disputes_resp = tc.get("/score-disputes")

    app_ok = (
        scores_resp.status_code == 200
        and len(scores_resp.json()["scores"]) == 1
        and disputes_resp.status_code == 200
        and len(disputes_resp.json()["disputes"]) == 1
    )

    if app_ok and bus_ok:
        print("PASS")
    else:
        print(f"FAIL (app={app_ok}, bus={bus_ok})")