# deps: fastapi, pydantic, sqlalchemy
"""Scoring Frequency API — per-server scoring cadence metrics.

Public endpoint (auth=public).  Reads from mcp_llm_axis_scores and mcp_server_registry
via app.db.  Computes frequency stats in Python for PostgreSQL portability.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_frequency_api"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class ServerFrequency(BaseModel):
    server_id: str
    name: str
    most_recent_score_at: datetime
    total_scores: int
    avg_scores_per_day: float
    days_since_last_score: int
    score_span_days: int

    model_config = ConfigDict(from_attributes=True)


class ScoringFrequencyResponse(BaseModel):
    servers: list[ServerFrequency]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/scoring/frequency",
    response_model=ScoringFrequencyResponse,
    summary="Get scoring frequency metrics per server",
    responses={200: {"description": "Frequency metrics for scored servers"}},
)
def get_scoring_frequency(
    server_id: Optional[str] = Query(default=None, description="Filter to a single server"),
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> ScoringFrequencyResponse:
    """
    Returns per-server scoring cadence metrics over the past *days*:
    - total_scores: how many axis-score rows in the window
    - most_recent_score_at: latest scored_at timestamp
    - avg_scores_per_day: total_scores / score_span_days
    - days_since_last_score: wall-clock days since most recent score
    - score_span_days: calendar days between first and last score
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    q = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("most_recent_score_at"),
            func.count(McpLlmAxisScore.id).label("total_scores"),
            func.min(McpLlmAxisScore.scored_at).label("first_score_at"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
    )

    if server_id:
        q = q.filter(McpLlmAxisScore.server_id == server_id)

    rows = q.group_by(
        McpLlmAxisScore.server_id,
        McpServerRegistry.name,
    ).all()

    now = datetime.now(timezone.utc)
    servers = []

    for row in rows:
        most_recent = row.most_recent_score_at
        first_score = row.first_score_at
        total = row.total_scores

        span_days = 1
        if most_recent and first_score:
            span_days = max((most_recent - first_score).days, 1)

        avg_per_day = round(total / span_days, 4)
        days_since = 0
        if most_recent:
            days_since = (now - most_recent).days

        servers.append(
            ServerFrequency(
                server_id=row.server_id,
                name=row.name,
                most_recent_score_at=most_recent,
                total_scores=total,
                avg_scores_per_day=avg_per_day,
                days_since_last_score=days_since,
                score_span_days=span_days,
            )
        )

    return ScoringFrequencyResponse(servers=servers)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)

    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id="srv1", name="Alpha", risk_tier="HIGH",
        ))
        sess.add(McpServerRegistry(
            server_id="srv2", name="Beta", risk_tier="MEDIUM",
        ))
        sess.add(McpServerRegistry(
            server_id="srv3", name="Gamma", risk_tier="LOW",
        ))
        # srv1: 3 scores across ~20 days
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk",
            scored_at=now - timedelta(days=20),
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk",
            scored_at=now - timedelta(days=10),
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk",
            scored_at=now - timedelta(days=5),
        ))
        # srv2: 1 score
        sess.add(McpLlmAxisScore(
            server_id="srv2", axis_name="overall_risk",
            scored_at=now - timedelta(days=2),
        ))
        # srv3: outside window (not counted)
        sess.add(McpLlmAxisScore(
            server_id="srv3", axis_name="overall_risk",
            scored_at=now - timedelta(days=100),
        ))
        sess.commit()

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Test 1: happy path — returns srv1 and srv2 (srv3 is outside 30-day window)
    resp = client.get("/api/scoring/frequency", params={"days": 30})
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if "servers" not in data:
        print("FAIL: 'servers' key missing from response", file=sys.stderr)
        sys.exit(1)

    srv_map = {s["server_id"]: s for s in data["servers"]}

    # srv1 checks
    if "srv1" not in srv_map:
        print("FAIL: srv1 not in response", file=sys.stderr)
        sys.exit(1)
    s1 = srv_map["srv1"]
    if s1["total_scores"] != 3:
        print(f"FAIL: srv1 total_scores expected 3, got {s1['total_scores']}", file=sys.stderr)
        sys.exit(1)
    if s1["name"] != "Alpha":
        print(f"FAIL: srv1 name expected 'Alpha', got {s1['name']}", file=sys.stderr)
        sys.exit(1)

    # Test 2: filter by server_id
    resp2 = client.get("/api/scoring/frequency", params={"server_id": "srv2", "days": 30})
    if resp2.status_code != 200:
        print(f"FAIL: server_id filter failed: {resp2.status_code}", file=sys.stderr)
        sys.exit(1)
    if len(resp2.json()["servers"]) != 1:
        print(f"FAIL: expected 1 result for srv2 filter, got {len(resp2.json()['servers'])}", file=sys.stderr)
        sys.exit(1)

    # Test 3: days=0 validation
    resp3 = client.get("/api/scoring/frequency", params={"days": 0})
    if resp3.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {resp3.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
