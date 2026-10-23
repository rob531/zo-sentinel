"""
services/staged/stale_score_alert_api/contract.py

FastAPI contract for the stale score alert API.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class AlertItem(BaseModel):
    server_id: str = Field(..., description="Unique identifier of the server")
    name: str = Field(..., description="Human‑readable server name")
    last_scored_at: datetime = Field(..., description="Timestamp of the most recent score")
    hours_ago: float = Field(..., description="Hours elapsed since the last score")
    risk_tier: str | None = Field(None, description="Risk tier of the server")


class StaleScoreAlertResponse(BaseModel):
    threshold_hours: int = Field(..., description="Threshold used for staleness detection")
    alerts: List[AlertItem] = Field(..., description="List of servers with stale scores")


@router.get(
    "/scoring/stale-alerts",
    response_model=StaleScoreAlertResponse,
    summary="Return servers whose latest LLM axis score is older than the threshold",
)
async def get_stale_score_alerts(
    threshold_hours: int = Query(72, ge=0, description="Staleness threshold in hours"),
    db: Session = Depends(get_session),
) -> StaleScoreAlertResponse:
    """
    Identify servers whose most recent `mcp_llm_axis_scores.scored_at` timestamp
    is older than *threshold_hours*.
    """
    cutoff = datetime.utcnow() - timedelta(hours=threshold_hours)

    # Sub‑query: latest scored_at per server
    latest_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Join with server registry and filter by cutoff
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            latest_subq.c.last_scored_at,
        )
        .join(latest_subq, McpServerRegistry.server_id == latest_subq.c.server_id)
        .where(latest_subq.c.last_scored_at < cutoff)
    )

    rows = db.execute(stmt).all()

    alerts: List[AlertItem] = []
    now = datetime.utcnow()
    for row in rows:
        last_scored_at: datetime = row.last_scored_at
        hours_ago = (now - last_scored_at).total_seconds() / 3600.0
        alerts.append(
            AlertItem(
                server_id=row.server_id,
                name=row.name,
                last_scored_at=last_scored_at,
                hours_ago=hours_ago,
                risk_tier=row.risk_tier,
            )
        )

    return StaleScoreAlertResponse(threshold_hours=threshold_hours, alerts=alerts)


# --------------------------------------------------------------------------- #
# Self‑test (run with `python -m services.staged.stale_score_alert_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # Build an in‑memory SQLite DB that mirrors the real models
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Populate test data
    # ------------------------------------------------------------------- #
    now = datetime.utcnow()
    with TestSessionLocal() as db:
        # Servers
        srv_fresh = McpServerRegistry(
            server_id="srv_fresh",
            name="Fresh Server",
            risk_tier="low",
        )
        srv_stale_a = McpServerRegistry(
            server_id="srv_stale_a",
            name="Stale Server A",
            risk_tier="high",
        )
        srv_stale_b = McpServerRegistry(
            server_id="srv_stale_b",
            name="Stale Server B",
            risk_tier="medium",
        )
        db.add_all([srv_fresh, srv_stale_a, srv_stale_b])
        db.flush()  # obtain PKs if needed

        # Scores
        db.add_all(
            [
                McpLlmAxisScore(
                    server_id="srv_fresh",
                    scored_at=now - timedelta(hours=24),  # fresh
                    axis_name="test",
                    adapter_sha256="a",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=1,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.0,
                    p_danger=0.0,
                    p_top=0.0,
                    probs="{}",
                ),
                McpLlmAxisScore(
                    server_id="srv_stale_a",
                    scored_at=now - timedelta(hours=100),  # stale
                    axis_name="test",
                    adapter_sha256="b",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=2,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.0,
                    p_danger=0.0,
                    p_top=0.0,
                    probs="{}",
                ),
                McpLlmAxisScore(
                    server_id="srv_stale_b",
                    scored_at=now - timedelta(hours=80),  # stale
                    axis_name="test",
                    adapter_sha256="c",
                    decision_rule_version="v1",
                    escalated=False,
                    escalated_to=None,
                    id=3,
                    label="label",
                    label_index=0,
                    model_version="m1",
                    p_critical=0.0,
                    p_danger=0.0,
                    p_top=0.0,
                    probs="{}",
                ),
            ]
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute acceptance test
    # ------------------------------------------------------------------- #
    resp = client.get("/api/scoring/stale-alerts?threshold_hours=72")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["threshold_hours"] == 72, "Threshold mismatch"
    alerts = data["alerts"]
    assert isinstance(alerts, list), "Alerts not a list"
    assert len(alerts) == 2, f"Expected 2 alerts, got {len(alerts)}"
    alert_ids = {a["server_id"] for a in alerts}
    assert "srv_fresh" not in alert_ids, "Fresh server incorrectly reported"
    assert {"srv_stale_a", "srv_stale_b"} == alert_ids, "Stale servers missing or extra"

    print("PASS")
    sys.exit(0)