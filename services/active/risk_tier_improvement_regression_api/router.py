"""
services/active/risk_tier_improvement_regression_api/router.py

GET /api/risk/improvement-regression?days=N

For each scored server, compare the overall_risk axis probabilities between the
most-recent score and the earliest score older than the cutoff window.

  - new       : no axis score older than the cutoff (server appeared in the window)
  - improved   : p_critical/p_danger fell (lower = better)
  - regressed  : p_critical/p_danger rose (higher = worse)
  - stable     : no meaningful change

Data comes from app.db + app.models (SQLAlchemy session).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_improvement_regression"])


# ---------- Pydantic schemas ----------
class ServerChange(BaseModel):
    server_id: str
    name: Optional[str]
    prev_label: Optional[str]
    curr_label: Optional[str]
    prev_p_critical: Optional[float]
    curr_p_critical: Optional[float]
    delta: Literal["improved", "regressed", "stable", "new"]


class ImprovementRegressionResponse(BaseModel):
    days: int
    improved: int
    regressed: int
    stable: int
    new: int
    servers: List[ServerChange]


# ---------- Core logic ----------
OVERALL_AXIS = "overall_risk"


def _risk_label(p_critical: float, p_danger: float) -> str:
    """Derive a qualitative label from axis probabilities (mirrors the SFT label head)."""
    if p_critical >= 0.5:
        return "CRITICAL"
    if p_critical >= 0.25:
        return "HIGH"
    if p_danger >= 0.5:
        return "HIGH"
    if p_danger >= 0.25:
        return "MEDIUM"
    return "LOW"


def _delta(p_prev: float, p_curr: float) -> Literal["improved", "regressed", "stable"]:
    diff = p_curr - p_prev
    if diff < -0.05:
        return "improved"
    if diff > 0.05:
        return "regressed"
    return "stable"


def get_risk_improvement_regression(
    days: int,
    session: Session,
) -> ImprovementRegressionResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    # All servers that have at least one overall_risk axis score
    servers = (
        session.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name).join(
                McpLlmAxisScore,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            ).where(
                McpLlmAxisScore.axis_name == OVERALL_AXIS
            ).distinct()
        ).all()
    )

    improved = regressed = stable = new_count = 0
    server_changes: List[ServerChange] = []

    for row in servers:
        server_id: str = row.server_id
        name: Optional[str] = row.name

        scores = (
            session.execute(
                select(McpLlmAxisScore)
                .where(
                    McpLlmAxisScore.server_id == server_id,
                    McpLlmAxisScore.axis_name == OVERALL_AXIS,
                )
                .order_by(McpLlmAxisScore.scored_at)
            ).scalars().all()
        )

        if not scores:
            continue

        # Most recent score
        curr = scores[-1]
        curr_p_crit = curr.p_critical or 0.0
        curr_label = _risk_label(curr.p_critical or 0.0, curr.p_danger or 0.0)

        # Earliest score older than the cutoff
        prev_score = None
        for s in scores:
            if s.scored_at is not None and s.scored_at <= cutoff:
                prev_score = s
                break

        if prev_score is None:
            # No historical score outside the window → newly discovered
            delta: Literal["improved", "regressed", "stable", "new"] = "new"
            new_count += 1
            server_changes.append(
                ServerChange(
                    server_id=server_id,
                    name=name,
                    prev_label=None,
                    curr_label=curr_label,
                    prev_p_critical=None,
                    curr_p_critical=curr_p_crit,
                    delta=delta,
                )
            )
            continue

        prev_p_crit = prev_score.p_critical or 0.0
        prev_label = _risk_label(prev_p_crit, prev_score.p_danger or 0.0)

        risk_delta = _delta(prev_p_crit, curr_p_crit)
        if risk_delta == "improved":
            improved += 1
        elif risk_delta == "regressed":
            regressed += 1
        else:
            stable += 1

        server_changes.append(
            ServerChange(
                server_id=server_id,
                name=name,
                prev_label=prev_label,
                curr_label=curr_label,
                prev_p_critical=prev_p_crit,
                curr_p_critical=curr_p_crit,
                delta=risk_delta,
            )
        )

    return ImprovementRegressionResponse(
        days=days,
        improved=improved,
        regressed=regressed,
        stable=stable,
        new=new_count,
        servers=server_changes,
    )


# ---------- FastAPI endpoint ----------
@router.get(
    "/risk/improvement-regression",
    response_model=ImprovementRegressionResponse,
    summary="Risk-tier improvement / regression over N days",
)
def improvement_regression(
    days: int = Query(7, ge=1, le=365, description="Lookback window in days"),
    session: Session = Depends(get_session),
) -> ImprovementRegressionResponse:
    """Return per-server risk-tier delta over the past *days* days."""
    return get_risk_improvement_regression(days, session)


# ---------- Self-test ----------
if __name__ == "__main__":
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    now = datetime.utcnow()
    old_ts = now - timedelta(days=10)
    recent_ts = now - timedelta(days=1)

    with TestSession() as db:
        # Server 1: improved (p_critical 0.7 -> 0.2)
        srv1 = McpServerRegistry(server_id="srv-improved", name="ImprovedSrv")
        db.add(srv1)
        db.add(McpLlmAxisScore(
            server_id="srv-improved", axis_name=OVERALL_AXIS,
            p_critical=0.7, p_danger=0.8,
            model_version="v1", scored_at=old_ts,
        ))
        db.add(McpLlmAxisScore(
            server_id="srv-improved", axis_name=OVERALL_AXIS,
            p_critical=0.2, p_danger=0.3,
            model_version="v1", scored_at=recent_ts,
        ))

        # Server 2: regressed (p_critical 0.2 -> 0.7)
        srv2 = McpServerRegistry(server_id="srv-regressed", name="RegressedSrv")
        db.add(srv2)
        db.add(McpLlmAxisScore(
            server_id="srv-regressed", axis_name=OVERALL_AXIS,
            p_critical=0.2, p_danger=0.3,
            model_version="v1", scored_at=old_ts,
        ))
        db.add(McpLlmAxisScore(
            server_id="srv-regressed", axis_name=OVERALL_AXIS,
            p_critical=0.7, p_danger=0.8,
            model_version="v1", scored_at=recent_ts,
        ))

        # Server 3: stable (p_critical 0.2 -> 0.22)
        srv3 = McpServerRegistry(server_id="srv-stable", name="StableSrv")
        db.add(srv3)
        db.add(McpLlmAxisScore(
            server_id="srv-stable", axis_name=OVERALL_AXIS,
            p_critical=0.2, p_danger=0.3,
            model_version="v1", scored_at=old_ts,
        ))
        db.add(McpLlmAxisScore(
            server_id="srv-stable", axis_name=OVERALL_AXIS,
            p_critical=0.22, p_danger=0.28,
            model_version="v1", scored_at=recent_ts,
        ))

        # Server 4: new (only scored within window)
        srv4 = McpServerRegistry(server_id="srv-new", name="NewSrv")
        db.add(srv4)
        db.add(McpLlmAxisScore(
            server_id="srv-new", axis_name=OVERALL_AXIS,
            p_critical=0.1, p_danger=0.2,
            model_version="v1", scored_at=recent_ts,
        ))

        db.commit()

    # Run the actual function
    from app.main import app

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    resp = client.get("/api/risk/improvement-regression?days=7")
    assert resp.status_code == 200, f"status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["days"] == 7
    assert data["improved"] == 1, f"expected improved=1, got {data['improved']}"
    assert data["regressed"] == 1, f"expected regressed=1, got {data['regressed']}"
    assert data["stable"] == 1, f"expected stable=1, got {data['stable']}"
    assert data["new"] == 1, f"expected new=1, got {data['new']}"
    assert len(data["servers"]) == 4

    by_name = {s["name"]: s["delta"] for s in data["servers"]}
    assert by_name["ImprovedSrv"] == "improved", by_name
    assert by_name["RegressedSrv"] == "regressed", by_name
    assert by_name["StableSrv"] == "stable", by_name
    assert by_name["NewSrv"] == "new", by_name

    app.dependency_overrides.clear()
    print("PASS")
