# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Delta Service.

Computes per-axis p_top deltas between the two most recent
assessments within N days for a given server.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_delta"])


class AxisDelta(BaseModel):
    axis_name: str
    current_p_top: float
    previous_p_top: float
    delta: float
    current_label: Optional[str] = None
    previous_label: Optional[str] = None


class RiskDeltaResponse(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    axes: list[AxisDelta]
    tier_change: Optional[str] = None
    days_between_assessments: Optional[float] = None
    current_risk_tier: Optional[str] = None


def compute_axis_deltas(
    session: Session,
    server_id: str,
    days: int,
) -> tuple[list[AxisDelta], Optional[float]]:
    """Return (axis_deltas, days_between_assessments)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Verify server exists
    server = session.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()
    if not server:
        raise ValueError(f"Server {server_id} not found")

    # Two most recent overall_risk scores (to anchor the time window)
    recent = (
        session.execute(
            select(McpLlmAxisScore)
            .where(McpLlmAxisScore.server_id == server_id)
            .where(McpLlmAxisScore.scored_at >= cutoff)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )

    if len(recent) < 2:
        return [], None

    # Most recent = index 0, previous = index 1
    current_time = recent[0].scored_at
    previous_time = recent[1].scored_at
    days_between = (current_time - previous_time).total_seconds() / 86400.0

    # Collect all distinct scored_at timestamps for current and previous windows
    def window_scores(ts):
        return [
            s for s in recent
            if abs((s.scored_at - ts).total_seconds()) < 3600
        ]

    current_scores = window_scores(current_time)
    previous_scores = window_scores(previous_time)

    # Build delta map by axis_name
    delta_map: dict[str, AxisDelta] = {}
    for s in current_scores:
        prev = next(
            (p for p in previous_scores if p.axis_name == s.axis_name),
            None,
        )
        delta_map[s.axis_name] = AxisDelta(
            axis_name=s.axis_name,
            current_p_top=s.p_top,
            previous_p_top=prev.p_top if prev else 0.0,
            delta=round(s.p_top - (prev.p_top if prev else 0.0), 4),
            current_label=s.label,
            previous_label=prev.label if prev else None,
        )

    return list(delta_map.values()), round(days_between, 2)


def determine_tier_change(deltas: list[AxisDelta]) -> Optional[str]:
    """Return 'regression', 'improvement', or 'stable' based on delta thresholds."""
    if not deltas:
        return None
    neg = [d for d in deltas if d.delta < -0.05]
    pos = [d for d in deltas if d.delta > 0.05]
    if neg:
        return "regression"
    if pos:
        return "improvement"
    return "stable"


@router.get("/risk/delta", response_model=RiskDeltaResponse)
def get_risk_delta(
    server_id: str = Query(..., description="Server identifier"),
    days: int = Query(default=30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_session),
) -> RiskDeltaResponse:
    """Return per-axis p_top deltas between the two most recent assessment windows."""
    try:
        deltas, days_between = compute_axis_deltas(db, server_id, days)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    tier_change = determine_tier_change(deltas)

    # Get server metadata
    server = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    ).scalar_one_or_none()

    return RiskDeltaResponse(
        server_id=server_id,
        server_name=server.name if server else None,
        axes=deltas,
        tier_change=tier_change,
        days_between_assessments=days_between,
        current_risk_tier=server.risk_tier if server else None,
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _root = str(Path(__file__).resolve().parents[3])
    if _root not in sys.path:
        sys.path.insert(0, _root)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed data
    now = datetime.now(timezone.utc)
    with TestSession() as db:
        db.add(McpServerRegistry(
            server_id="srv-delta-1",
            name="Delta Test Server",
            risk_tier="MEDIUM",
            registry_source="test",
        ))
        db.add(McpLlmAxisScore(
            id=1,
            server_id="srv-delta-1",
            axis_name="overall_risk",
            label="MEDIUM",
            label_index=2,
            p_top=0.55,
            scored_at=now - timedelta(days=7),
            model_version="v1",
        ))
        db.add(McpLlmAxisScore(
            id=2,
            server_id="srv-delta-1",
            axis_name="overall_risk",
            label="HIGH",
            label_index=3,
            p_top=0.70,
            scored_at=now - timedelta(days=1),
            model_version="v1",
        ))
        db.add(McpLlmAxisScore(
            id=3,
            server_id="srv-delta-1",
            axis_name="auth_strength",
            label="LOW",
            label_index=1,
            p_top=0.20,
            scored_at=now - timedelta(days=7),
            model_version="v1",
        ))
        db.add(McpLlmAxisScore(
            id=4,
            server_id="srv-delta-1",
            axis_name="auth_strength",
            label="LOW",
            label_index=1,
            p_top=0.25,
            scored_at=now - timedelta(days=1),
            model_version="v1",
        ))
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/risk/delta?server_id=srv-delta-1&days=30")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["server_id"] == "srv-delta-1"
    assert data["server_name"] == "Delta Test Server"
    assert data["current_risk_tier"] == "MEDIUM"
    assert data["tier_change"] == "improvement"
    assert len(data["axes"]) == 2

    axis_map = {a["axis_name"]: a for a in data["axes"]}

    overall = axis_map["overall_risk"]
    assert overall["current_p_top"] == 0.70
    assert overall["previous_p_top"] == 0.55
    assert overall["delta"] == 0.15

    auth = axis_map["auth_strength"]
    assert auth["current_p_top"] == 0.25
    assert auth["previous_p_top"] == 0.20
    assert auth["delta"] == 0.05

    # Test 404 for unknown server
    resp404 = client.get("/api/risk/delta?server_id=unknown-srv&days=30")
    assert resp404.status_code == 404

    # Test server with no scores
    with TestSession() as db:
        db.add(McpServerRegistry(
            server_id="srv-no-scores",
            name="No Scores Server",
            registry_source="test",
        ))
        db.commit()

    resp_empty = client.get("/api/risk/delta?server_id=srv-no-scores&days=30")
    assert resp_empty.status_code == 200
    data_empty = resp_empty.json()
    assert data_empty["axes"] == []
    assert data_empty["days_between_assessments"] is None

    print("PASS")
    sys.exit(0)
