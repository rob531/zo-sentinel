# deps: fastapi, pydantic, sqlalchemy
"""Router for risk_tier_breach_alert_api -- detect servers whose risk tier has degraded."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api", tags=["risk_tier_breach_alert_api"])


# --- Tier helpers -----------------------------------------------------------

_TIER_THRESHOLDS = [
    (0.85, "TRUSTED_GENERAL"),
    (0.70, "CAUTION_LIMITED"),
    (0.50, "CAUTION_MONITORING"),
    (0.25, "WARNING_RESTRICTED"),
]


def _tier_from_p_top(p_top: Optional[float]) -> str:
    if p_top is None:
        return "UNKNOWN"
    for threshold, tier in _TIER_THRESHOLDS:
        if p_top >= threshold:
            return tier
    return "DANGER_PROHIBITED"


# --- Pydantic models -------------------------------------------------------

class TierBreachAlert(BaseModel):
    server_id: str
    server_name: Optional[str] = None
    old_tier: str
    new_tier: str
    changed_at: datetime
    confidence: Optional[float] = None
    current_p_top: Optional[float] = None


class TierBreachResponse(BaseModel):
    breach_count: int
    alerts: list[TierBreachAlert]


# --- Endpoint --------------------------------------------------------------

@router.get("/risk/tier-breach-alerts", response_model=TierBreachResponse)
def get_tier_breach_alerts(
    days: int = Query(7, ge=1, le=90, description="Look-back window in days"),
    session: Session = Depends(get_session),
) -> TierBreachResponse:
    """
    Return servers whose risk tier has degraded relative to the oldest
    `overall_risk` axis score within the look-back window.

    A breach is detected when the current `overall_risk` p_top maps to a
    different tier bucket than the oldest qualifying baseline score for the
    same server.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Sub-query: oldest baseline score per server within the window
    baseline_subq = (
        session.query(
            McpLlmAxisScore.server_id,
            func.min(McpLlmAxisScore.scored_at).label("baseline_at"),
        )
        .filter(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Sub-query: most recent score per server within the window
    latest_subq = (
        session.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Fetch baseline scores
    baseline_q = (
        session.query(McpLlmAxisScore)
        .join(
            baseline_subq,
            (McpLlmAxisScore.server_id == baseline_subq.c.server_id)
            & (McpLlmAxisScore.scored_at == baseline_subq.c.baseline_at)
            & (McpLlmAxisScore.axis_name == "overall_risk"),
        )
        .all()
    )
    baseline_by_server: dict[str, tuple[float, datetime]] = {
        r.server_id: (float(r.p_top), r.scored_at) for r in baseline_q if r.p_top is not None
    }

    # Fetch latest scores
    latest_q = (
        session.query(McpLlmAxisScore)
        .join(
            latest_subq,
            (McpLlmAxisScore.server_id == latest_subq.c.server_id)
            & (McpLlmAxisScore.scored_at == latest_subq.c.latest_at)
            & (McpLlmAxisScore.axis_name == "overall_risk"),
        )
        .all()
    )
    latest_by_server: dict[str, tuple[float, datetime]] = {
        r.server_id: (float(r.p_top), r.scored_at) for r in latest_q if r.p_top is not None
    }

    # Identify servers with a tier change
    changed_server_ids = [
        sid for sid in latest_by_server
        if sid in baseline_by_server
        and _tier_from_p_top(latest_by_server[sid][0])
            != _tier_from_p_top(baseline_by_server[sid][0])
    ]

    if not changed_server_ids:
        return TierBreachResponse(breach_count=0, alerts=[])

    # Fetch server metadata
    server_rows = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id.in_(changed_server_ids))
        .all()
    )
    registry = {r.server_id: r for r in server_rows}

    alerts = []
    for sid in changed_server_ids:
        base_p_top, base_at = baseline_by_server[sid]
        curr_p_top, curr_at = latest_by_server[sid]
        srv = registry.get(sid)
        alerts.append(
            TierBreachAlert(
                server_id=sid,
                server_name=srv.name if srv else None,
                old_tier=_tier_from_p_top(base_p_top),
                new_tier=_tier_from_p_top(curr_p_top),
                changed_at=curr_at,
                confidence=srv.confidence if srv else None,
                current_p_top=curr_p_top,
            )
        )

    alerts.sort(key=lambda a: a.changed_at, reverse=True)
    return TierBreachResponse(breach_count=len(alerts), alerts=alerts)


# --- Self-test -------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Ensure repo root is on path
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Import ORM models via __import__ to avoid app/__init__ issues
    app_models = __import__("app.models", fromlist=["Base", "McpServerRegistry", "McpLlmAxisScore"])
    Base = app_models.Base
    McpServerRegistry = app_models.McpServerRegistry
    McpLlmAxisScore = app_models.McpLlmAxisScore

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    baseline_time = now - timedelta(days=2)

    # Seed data using raw SQL for SQLite compatibility
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO mcp_server_registry
                (server_id, name, risk_tier, confidence, registry_source)
            VALUES
                ('srv-001', 'Alpha Server',  'TRUSTED_GENERAL', 0.95, 'npm'),
                ('srv-002', 'Beta Server',  'TRUSTED_GENERAL', 0.88, 'github'),
                ('srv-003', 'Gamma Server', 'CAUTION_LIMITED', 0.72, 'npm')
        """),)
        conn.commit()

        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, p_top, label, scored_at, model_version)
            VALUES
                ('srv-001', 'overall_risk', 0.92, 'TRUSTED_GENERAL', :baseline, 'v1'),
                ('srv-002', 'overall_risk', 0.90, 'TRUSTED_GENERAL', :baseline, 'v1'),
                ('srv-001', 'overall_risk', 0.60, 'CAUTION_MONITORING', :now, 'v1'),
                ('srv-003', 'overall_risk', 0.40, 'WARNING_RESTRICTED', :now, 'v1')
        """), {"baseline": baseline_time, "now": now})
        conn.commit()

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    resp = client.get("/api/risk/tier-breach-alerts?days=7")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["breach_count"] >= 1, f"Expected breach_count >= 1, got {data['breach_count']}"
    ids = {a["server_id"] for a in data["alerts"]}
    assert "srv-001" in ids or "srv-003" in ids, f"Expected srv-001 or srv-003 in alerts, got {ids}"
    for a in data["alerts"]:
        assert a["old_tier"] != a["new_tier"], f"Same tier for {a['server_id']}"

    # Also test with a zero-result window
    resp2 = client.get("/api/risk/tier-breach-alerts?days=1")
    assert resp2.status_code == 200, f"Got {resp2.status_code}"

    print("PASS")
    sys.exit(0)
