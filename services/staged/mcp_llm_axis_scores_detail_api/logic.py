"""Logic for the MCP LLM Axis Scores Detail API.

Provides a FastAPI endpoint that returns all LLM axis scores for a given
server, together with server metadata.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class AxisScoreItem(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    probs: List[float]
    escalated: bool
    decision_rule_version: str
    model_version: str
    scored_at: datetime

    class Config:
        orm_mode = True


class AxisScoresResponse(BaseModel):
    server_id: str
    server_name: str
    risk_tier: Optional[str]
    axes: List[AxisScoreItem]

    class Config:
        orm_mode = True


# --------------------------------------------------------------------------- #
# Core business logic
# --------------------------------------------------------------------------- #


def get_axis_scores_detail(
    server_id: str, session=Depends(get_session)
) -> AxisScoresResponse:
    """Return all LLM axis scores for *server_id* ordered by ``scored_at`` DESC.

    The response also contains the server name and risk tier taken from
    ``McpServerRegistry``.
    """
    server: McpServerRegistry = (
        session.query(McpServerRegistry).filter_by(server_id=server_id).first()
    )
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")

    scores: List[McpLlmAxisScore] = (
        session.query(McpLlmAxisScore)
        .filter_by(server_id=server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    axes = [
        AxisScoreItem.from_orm(score)
        for score in scores
    ]

    return AxisScoresResponse(
        server_id=server.server_id,
        server_name=server.name,
        risk_tier=server.risk_tier,
        axes=axes,
    )


# --------------------------------------------------------------------------- #
# FastAPI router (exposed for the service)
# --------------------------------------------------------------------------- #

app = FastAPI()


@app.get(
    "/api/servers/{server_id}/axis-scores",
    response_model=AxisScoresResponse,
    tags=["mcp_llm_axis_scores_detail_api"],
)
def axis_scores_endpoint(server_id: str, session=Depends(get_session)):
    """GET /api/servers/{server_id}/axis-scores"""
    return get_axis_scores_detail(server_id, session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # NOTE: The self‑test builds an in‑memory SQLite DB, seeds it with data,
    # overrides the ``get_session`` dependency and exercises the endpoint.
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and initialise tables
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    # ------------------------------------------------------------------- #
    # Seed data
    # ------------------------------------------------------------------- #
    db = SessionLocal()

    # Two servers
    server1 = McpServerRegistry(
        server_id="srv1",
        name="Server One",
        risk_tier="high",
        confidence=0.9,
        description="",
        first_seen=datetime.utcnow(),
        last_assessed=datetime.utcnow(),
        last_scanned=datetime.utcnow(),
        last_seen=datetime.utcnow(),
        meta={},
        registry_source="test",
        scan_count=0,
        trust_score=0.0,
        url="",
        verdict="",
        verdict_reasoning="",
    )
    server2 = McpServerRegistry(
        server_id="srv2",
        name="Server Two",
        risk_tier="low",
        confidence=0.8,
        description="",
        first_seen=datetime.utcnow(),
        last_assessed=datetime.utcnow(),
        last_scanned=datetime.utcnow(),
        last_seen=datetime.utcnow(),
        meta={},
        registry_source="test",
        scan_count=0,
        trust_score=0.0,
        url="",
        verdict="",
        verdict_reasoning="",
    )
    db.add_all([server1, server2])
    db.flush()

    # Axis names (7 distinct axes)
    axis_names = [
        "confidentiality",
        "integrity",
        "availability",
        "authenticity",
        "non_repudiation",
        "privacy",
        "compliance",
    ]

    # Helper to create a score row
    def make_score(server_id: str, ts: datetime, axis: str, p_top: float) -> McpLlmAxisScore:
        return McpLlmAxisScore(
            adapter_sha256="dummysha",
            axis_name=axis,
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=None,
            label="label",
            label_index=0,
            model_version="model-1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=p_top,
            probs=[p_top, 0.0, 0.0],
            scored_at=ts,
            server_id=server_id,
        )

    now = datetime.utcnow()
    # For each server, three timestamps, each with 7 axes
    for srv in ("srv1", "srv2"):
        for i in range(3):
            ts = now.replace(microsecond=0)  # deterministic
            ts = ts.replace(second=ts.second - i)  # different seconds
            for idx, axis in enumerate(axis_names):
                # Use a known p_top for the first axis of the latest timestamp
                p_top_val = 0.99 if (i == 0 and idx == 0) else 0.5
                db.add(make_score(srv, ts, axis, p_top_val))

    db.commit()

    # ------------------------------------------------------------------- #
    # Override the dependency to use the test session
    # ------------------------------------------------------------------- #
    def get_test_session():
        return db

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform the request and validate the contract
    # ------------------------------------------------------------------- #
    resp = client.get("/api/servers/srv1/axis-scores")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    axes = payload.get("axes", [])
    assert len(axes) >= 7, f"Expected at least 7 axes, got {len(axes)}"
    # The first axis of the latest score should have p_top == 0.99
    assert abs(axes[0]["p_top"] - 0.99) < 1e-6, "p_top value mismatch"

    print("PASS")