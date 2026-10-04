# deps: fastapi, sqlalchemy, pydantic, requests
"""Verdict Comparison Service

Provides an endpoint to compare LLM axis scores between two MCP servers.
Publicly accessible (no auth required). Uses the shared DB session from
`app.db` and the ORM models from `app.models`.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Dict
from sqlalchemy.orm import Session
from sqlalchemy import func

# Import the shared DB session dependency and ORM models
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/verdict_comparison", tags=["verdict_comparison"])

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------
class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float | None = None
    p_critical: float | None = None
    p_danger: float | None = None

    model_config = ConfigDict(from_attributes=True)

class ServerComparison(BaseModel):
    server_id: int
    server_name: str | None = None
    scores: List[AxisScore]

class ComparisonResponse(BaseModel):
    server1: ServerComparison
    server2: ServerComparison

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
def _fetch_latest_scores(db: Session, server_id: int) -> List[McpLlmAxisScore]:
    """Return the most recent axis scores for a given server.

    The latest version is determined by the highest `model_version` and the most
    recent `scored_at` timestamp for each axis.
    """
    subq = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.model_version).label("max_version"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    # Join back to get the latest row per axis
    latest = (
        db.query(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.model_version == subq.c.max_version),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    return latest

def _server_info(db: Session, server_id: int) -> McpServerRegistry:
    srv = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return srv

# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@router.get("/compare", response_model=ComparisonResponse)
def compare_servers(
    server_id_1: int = Query(..., alias="server1"),
    server_id_2: int = Query(..., alias="server2"),
    db: Session = Depends(get_session),
):
    """Compare LLM axis scores between two servers.

    Returns the latest scores for each axis for both servers.
    """
    if server_id_1 == server_id_2:
        raise HTTPException(status_code=400, detail="server1 and server2 must differ")

    srv1 = _server_info(db, server_id_1)
    srv2 = _server_info(db, server_id_2)

    scores1 = _fetch_latest_scores(db, server_id_1)
    scores2 = _fetch_latest_scores(db, server_id_2)

    def to_axis(score: McpLlmAxisScore) -> AxisScore:
        return AxisScore(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top,
            p_critical=score.p_critical,
            p_danger=score.p_danger,
        )

    comp1 = ServerComparison(
        server_id=srv1.server_id,
        server_name=getattr(srv1, "name", None),
        scores=[to_axis(s) for s in scores1],
    )
    comp2 = ServerComparison(
        server_id=srv2.server_id,
        server_name=getattr(srv2, "name", None),
        scores=[to_axis(s) for s in scores2],
    )
    return ComparisonResponse(server1=comp1, server2=comp2)

# ---------------------------------------------------------------------------
# Self‑test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # Assuming Base is exported for test DB creation

    # Create an in‑memory SQLite DB and override the dependency
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = router  # FastAPI router is also an ASGI app
    client = TestClient(app)
    app.dependency_overrides[get_session] = override_get_session

    # Insert minimal test data
    with TestSessionLocal() as db:
        # Create two servers
        srv1 = McpServerRegistry(server_id=1, name="SrvOne")
        srv2 = McpServerRegistry(server_id=2, name="SrvTwo")
        db.add_all([srv1, srv2])
        # Add a couple of axis scores for each server
        score1 = McpLlmAxisScore(
            server_id=1,
            axis_name="overall_risk",
            label="low",
            p_top=0.7,
            p_critical=0.2,
            p_danger=0.1,
            model_version=1,
        )
        score2 = McpLlmAxisScore(
            server_id=2,
            axis_name="overall_risk",
            label="high",
            p_top=0.3,
            p_critical=0.5,
            p_danger=0.2,
            model_version=1,
        )
        db.add_all([score1, score2])
        db.commit()

    # Happy‑path request
    resp = client.get("/compare?server1=1&server2=2")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    if data["server1"]["server_id"] != 1 or data["server2"]["server_id"] != 2:
        print("FAIL: server IDs mismatch")
        sys.exit(1)
    print("PASS")
