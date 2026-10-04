# deps: fastapi, pydantic, sqlalchemy
"""Scoring History API — per-day axis score history for a server.

Public endpoint (auth=public).  Reads from mcp_llm_axis_scores via app.db.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_history_api"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class AxisScoreEntry(BaseModel):
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    model_version: str
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DaySnapshot(BaseModel):
    date: str
    axes: list[AxisScoreEntry]


class ScoringHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    days: int
    series: list[DaySnapshot]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/scoring/history",
    response_model=ScoringHistoryResponse,
    summary="Get per-day scoring history for a server",
    responses={404: {"description": "Server not found"}},
)
def get_scoring_history(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_session),
) -> ScoringHistoryResponse:
    """
    Returns per-day axis score snapshots for the given server over the past *days*.
    """
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    # Group by calendar date
    by_date: dict[str, list[McpLlmAxisScore]] = {}
    for row in rows:
        key = row.scored_at.strftime("%Y-%m-%d")
        by_date.setdefault(key, []).append(row)

    series = [
        DaySnapshot(
            date=date,
            axes=[
                AxisScoreEntry(
                    axis_name=r.axis_name,
                    label=r.label,
                    label_index=r.label_index,
                    p_top=r.p_top,
                    p_critical=r.p_critical,
                    p_danger=r.p_danger,
                    escalated=r.escalated,
                    model_version=r.model_version,
                    scored_at=r.scored_at,
                )
                for r in sorted(axis_rows, key=lambda x: x.axis_name)
            ],
        )
        for date, axis_rows in sorted(by_date.items(), reverse=True)
    ]

    return ScoringHistoryResponse(
        server_id=server_id,
        name=server.name,
        days=days,
        series=series,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base
    from app.db import get_session as _original_get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_original_get_session] = _override

    now = datetime.now(timezone.utc)
    d1 = now - timedelta(days=1)
    d2 = now - timedelta(days=2)

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="srv1", name="Test Server", risk_tier="HIGH",
            verdict="clean", confidence=0.9,
        ))
        sess.add(McpServerRegistry(
            server_id="srv2", name="Other Server", risk_tier="LOW",
            verdict="clean", confidence=0.8,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk", label="HIGH",
            p_top=0.75, p_critical=0.1, p_danger=0.15,
            model_version="v1", adapter_sha256="a1", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="auth_strength", label="MEDIUM",
            p_top=0.45, p_critical=0.2, p_danger=0.35,
            model_version="v1", adapter_sha256="a1", decision_rule_version="r1",
            scored_at=d2, escalated=False,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="overall_risk", label="CRITICAL",
            p_top=0.88, p_critical=0.05, p_danger=0.07,
            model_version="v1", adapter_sha256="a2", decision_rule_version="r1",
            scored_at=d1, escalated=True,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv1", axis_name="auth_strength", label="LOW",
            p_top=0.3, p_critical=0.3, p_danger=0.4,
            model_version="v1", adapter_sha256="a2", decision_rule_version="r1",
            scored_at=d1, escalated=False,
        ))
        sess.commit()

    client = TestClient(app)

    # Test 1: happy path — scoring history for srv1
    resp = client.get("/api/scoring/history", params={"server_id": "srv1", "days": 7})
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv1":
        print(f"FAIL: wrong server_id {data['server_id']}", file=sys.stderr)
        sys.exit(1)
    if len(data["series"]) != 2:
        print(f"FAIL: expected 2 days, got {len(data['series'])}", file=sys.stderr)
        sys.exit(1)
    # Latest day should have CRITICAL
    latest = data["series"][0]
    if latest["date"] != d1.strftime("%Y-%m-%d"):
        print(f"FAIL: expected latest date {d1.date()}, got {latest['date']}", file=sys.stderr)
        sys.exit(1)
    axis_names = {ax["axis_name"] for ax in latest["axes"]}
    if "overall_risk" not in axis_names:
        print(f"FAIL: overall_risk not in axes", file=sys.stderr)
        sys.exit(1)

    # Test 2: 404 for unknown server
    resp2 = client.get("/api/scoring/history", params={"server_id": "no-such-server", "days": 7})
    if resp2.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp2.status_code}", file=sys.stderr)
        sys.exit(1)

    # Test 3: empty series for server with no scores
    resp3 = client.get("/api/scoring/history", params={"server_id": "srv2", "days": 7})
    if resp3.status_code != 200:
        print(f"FAIL: expected 200 for srv2 (no scores), got {resp3.status_code}", file=sys.stderr)
        sys.exit(1)
    if len(resp3.json()["series"]) != 0:
        print(f"FAIL: expected 0 series for srv2", file=sys.stderr)
        sys.exit(1)

    # Test 4: days param validation — out of range
    resp4 = client.get("/api/scoring/history", params={"server_id": "srv1", "days": 0})
    if resp4.status_code != 422:
        print(f"FAIL: expected 422 for days=0, got {resp4.status_code}", file=sys.stderr)
        sys.exit(1)

    print("PASS")
