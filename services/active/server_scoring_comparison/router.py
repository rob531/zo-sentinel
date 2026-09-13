# deps: fastapi, sqlalchemy, pydantic, requests, pyjwt, passlib
"""Server Scoring Comparison Service

Provides an endpoint to compare LLM axis scores between two servers.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Dict, Any
from sqlalchemy.orm import Session

# Import the shared DB session and ORM models
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/server_scoring_comparison", tags=["server_scoring_comparison"])


# Pydantic models
class AxisScore(BaseModel):
    axis_name: str
    label: str | None = None
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None
    escalated: bool | None = None


class ServerInfo(BaseModel):
    server_id: str
    name: str | None = None
    url: str | None = None
    trust_score: float | None = None
    axis_scores: List[AxisScore] = Field(default_factory=list)


class ComparisonResponse(BaseModel):
    server_a: ServerInfo
    server_b: ServerInfo
    differences: Dict[str, Any] = Field(default_factory=dict)


def _fetch_server_info(db: Session, server_id: str) -> ServerInfo:
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    axis_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    axis_scores = [
        AxisScore(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            escalated=row.escalated,
        )
        for row in axis_rows
    ]
    return ServerInfo(
        server_id=server.server_id,
        name=server.name,
        url=server.url,
        trust_score=server.trust_score,
        axis_scores=axis_scores,
    )


@router.get("/compare", response_model=ComparisonResponse)
def compare_servers(
    server_id_a: str = Query(..., description="First server ID"),
    server_id_b: str = Query(..., description="Second server ID"),
    db: Session = Depends(get_session),
):
    if server_id_a == server_id_b:
        raise HTTPException(status_code=400, detail="server_id_a and server_id_b must differ")
    info_a = _fetch_server_info(db, server_id_a)
    info_b = _fetch_server_info(db, server_id_b)

    diffs: Dict[str, Any] = {}
    scores_a = {s.axis_name: s for s in info_a.axis_scores}
    scores_b = {s.axis_name: s for s in info_b.axis_scores}
    all_axes = set(scores_a) | set(scores_b)
    for axis in all_axes:
        a = scores_a.get(axis)
        b = scores_b.get(axis)
        diffs[axis] = {
            "label_a": a.label if a else None,
            "label_b": b.label if b else None,
            "p_top_diff": (a.p_top - b.p_top) if (a and b and a.p_top is not None and b.p_top is not None) else None,
        }
    return ComparisonResponse(server_a=info_a, server_b=info_b, differences=diffs)


# Self-test
if __name__ == "__main__":
    import sys
    import os
    # Ensure project root is on sys.path so 'app' resolves
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session():
        return TestSession()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session
    client = TestClient(app)

    try:
        # No data seeded → both servers 404
        resp = client.get("/compare?server_id_a=svc1&server_id_b=svc2")
        if resp.status_code != 404:
            raise AssertionError(f"Expected 404 for missing servers, got {resp.status_code}")
        # Same ID → 400
        resp2 = client.get("/compare?server_id_a=svc1&server_id_b=svc1")
        if resp2.status_code != 400:
            raise AssertionError(f"Expected 400 for same IDs, got {resp2.status_code}")
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)
