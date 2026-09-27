# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Transition Alert Service.

Public endpoint: returns servers that recently changed risk tier, with an alert
severity score. Data from app Postgres via get_session + SQLAlchemy models.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["risk_tier_transition_alert"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class TierChangeEntry(BaseModel):
    server_id: str
    server_name: Optional[str]
    old_tier: str
    new_tier: str
    direction: str
    changed_at: str


class RiskTierTransitionAlertResponse(BaseModel):
    period_days: int
    alert_count: int
    alert_severity: str
    servers: List[TierChangeEntry]
    as_of: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _tier_rank(tier: str) -> int:
    ranks = {
        "CRITICAL": 5,
        "HIGH": 4,
        "MEDIUM": 3,
        "LOW": 2,
        "MINIMAL": 1,
        "TRUSTED": 0,
        "UNKNOWN": -1,
    }
    return ranks.get(tier.upper(), -1)


def _change_direction(old_tier: str, new_tier: str) -> str:
    old_rank = _tier_rank(old_tier)
    new_rank = _tier_rank(new_tier)
    if new_rank > old_rank:
        return "escalation"
    if new_rank < old_rank:
        return "de_escalation"
    return "lateral"


def _alert_severity(changes: List[PerspectiveEvent], from_tier: str, to_tier: str) -> str:
    if not changes:
        return "none"
    rank_delta = abs(_tier_rank(to_tier) - _tier_rank(from_tier))
    if rank_delta >= 3 or "CRITICAL" in (from_tier.upper(), to_tier.upper()):
        return "critical"
    if rank_delta >= 2 or "HIGH" in (from_tier.upper(), to_tier.upper()):
        return "high"
    if rank_delta >= 1:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/risk/tier-transition-alert",
    response_model=RiskTierTransitionAlertResponse,
    summary="Get risk tier transition alerts",
)
def get_tier_alerts(
    period_days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> RiskTierTransitionAlertResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    # Get perspective events for tier changes
    events = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.change_type == "tier_change")
        .filter(PerspectiveEvent.created_at >= cutoff)
        .order_by(PerspectiveEvent.created_at.desc())
        .all()
    )

    if not events:
        return RiskTierTransitionAlertResponse(
            period_days=period_days,
            alert_count=0,
            alert_severity="none",
            servers=[],
            as_of=datetime.now(timezone.utc).isoformat(),
        )

    # Collect unique server_ids
    server_ids = list({e.server_id for e in events})

    # Fetch server names
    server_rows = (
        db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name).where(
                McpServerRegistry.server_id.in_(server_ids)
            )
        ).all()
    )
    name_map = {r.server_id: r.name for r in server_rows}

    # Build entries
    servers: List[TierChangeEntry] = []
    for e in events:
        old_t = (e.old_tier or "UNKNOWN").upper()
        new_t = (e.new_tier or "UNKNOWN").upper()
        servers.append(
            TierChangeEntry(
                server_id=e.server_id,
                server_name=name_map.get(e.server_id),
                old_tier=old_t,
                new_tier=new_t,
                direction=_change_direction(old_t, new_t),
                changed_at=e.created_at.isoformat() if e.created_at else "",
            )
        )

    # Determine overall alert severity from most significant change
    top_severity = "none"
    severity_order = ["none", "low", "medium", "high", "critical"]
    for e in events:
        old_t = (e.old_tier or "UNKNOWN").upper()
        new_t = (e.new_tier or "UNKNOWN").upper()
        sev = _alert_severity(events, old_t, new_t)
        if severity_order.index(sev) > severity_order.index(top_severity):
            top_severity = sev

    return RiskTierTransitionAlertResponse(
        period_days=period_days,
        alert_count=len(servers),
        alert_severity=top_severity,
        servers=servers,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    with TestSession() as sess:
        sess.add(McpServerRegistry(
            server_id="srv-a", name="Alpha", risk_tier="HIGH",
            registry_source="test", url="http://a", description="a",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-b", name="Beta", risk_tier="LOW",
            registry_source="test", url="http://b", description="b",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-c", name="Gamma", risk_tier="MEDIUM",
            registry_source="test", url="http://c", description="c",
        ))
        # escalation
        sess.add(PerspectiveEvent(
            perspective_id="p1", server_id="srv-a",
            change_type="tier_change", old_tier="LOW", new_tier="HIGH",
            created_at=now - timedelta(hours=2),
        ))
        # de-escalation
        sess.add(PerspectiveEvent(
            perspective_id="p1", server_id="srv-b",
            change_type="tier_change", old_tier="HIGH", new_tier="LOW",
            created_at=now - timedelta(hours=5),
        ))
        # lateral (no rank change)
        sess.add(PerspectiveEvent(
            perspective_id="p1", server_id="srv-c",
            change_type="tier_change", old_tier="MEDIUM", new_tier="MEDIUM",
            created_at=now - timedelta(hours=1),
        ))
        # unrelated event (not a tier_change) -- should be excluded
        sess.add(PerspectiveEvent(
            perspective_id="p1", server_id="srv-a",
            change_type="added",
            created_at=now - timedelta(hours=1),
        ))
        sess.commit()

    c = TestClient(app)

    # Test: returns alerts
    r = c.get("/api/risk/tier-transition-alert?period_days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alert_count"] == 3, f"expected 3 alerts, got {body}"
    assert body["alert_severity"] in ("high", "medium", "critical"), body
    assert len(body["servers"]) == 3, body

    # Verify escalation is detected
    escalation = next((s for s in body["servers"] if s["server_id"] == "srv-a"), None)
    assert escalation is not None
    assert escalation["old_tier"] == "LOW"
    assert escalation["new_tier"] == "HIGH"
    assert escalation["direction"] == "escalation"

    # Verify de-escalation
    deesc = next((s for s in body["servers"] if s["server_id"] == "srv-b"), None)
    assert deesc is not None
    assert deesc["direction"] == "de_escalation"

    # Verify lateral
    lateral = next((s for s in body["servers"] if s["server_id"] == "srv-c"), None)
    assert lateral is not None
    assert lateral["direction"] == "lateral"

    # Test: empty result
    r2 = c.get("/api/risk/tier-transition-alert?period_days=1")
    assert r2.status_code == 200
    assert r2.json()["alert_count"] == 0
    assert r2.json()["alert_severity"] == "none"

    print("PASS")
    sys.exit(0)
