# deps: fastapi, pydantic, sqlalchemy
"""server_risk_tier_change_alert — alert on servers whose risk tier has changed.

GET /api/alerts/server-risk-tier-change
  Returns servers that recently changed risk tier based on overall_risk axis
  scores in mcp_llm_axis_scores, with direction and severity.

Auth: public.
Data: app-db via get_session + McpLlmAxisScore + McpServerRegistry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ensure repo root is on path so `app` imports resolve
_repo_root = str(Path(__file__).resolve().parents[3])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_tier_change_alert"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class TierChangeDetail(BaseModel):
    server_id: str
    server_name: Optional[str]
    old_tier: str
    new_tier: str
    direction: str
    scored_at: str


class ServerRiskTierChangeAlertResponse(BaseModel):
    period_days: int
    alert_count: int
    alert_severity: str
    servers: List[TierChangeDetail]
    as_of: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_TIER_RANKS = {
    "CRITICAL": 5,
    "HIGH": 4,
    "MEDIUM": 3,
    "LOW": 2,
    "MINIMAL": 1,
    "TRUSTED": 0,
    "UNKNOWN": -1,
}


def _tier_rank(tier: str) -> int:
    return _TIER_RANKS.get(tier.upper(), -1)


def _normalize_label(label: Optional[str]) -> str:
    if label is None:
        return "UNKNOWN"
    l = label.upper()
    if l in _TIER_RANKS:
        return l
    return "UNKNOWN"


def _change_direction(old_tier: str, new_tier: str) -> str:
    old_r = _tier_rank(old_tier)
    new_r = _tier_rank(new_tier)
    if new_r > old_r:
        return "escalation"
    if new_r < old_r:
        return "de_escalation"
    return "lateral"


def _overall_severity(changes: List[TierChangeDetail]) -> str:
    if not changes:
        return "none"
    order = ["none", "low", "medium", "high", "critical"]
    top = "none"
    for c in changes:
        rank_delta = abs(_tier_rank(c.new_tier) - _tier_rank(c.old_tier))
        if rank_delta >= 3 or c.old_tier == "CRITICAL" or c.new_tier == "CRITICAL":
            sev = "critical"
        elif rank_delta >= 2 or c.old_tier == "HIGH" or c.new_tier == "HIGH":
            sev = "high"
        elif rank_delta >= 1:
            sev = "medium"
        else:
            sev = "low"
        if order.index(sev) > order.index(top):
            top = sev
    return top


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/alerts/server-risk-tier-change",
    response_model=ServerRiskTierChangeAlertResponse,
    summary="Alert on servers whose risk tier has changed",
)
def get_server_risk_tier_change_alert(
    period_days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_session),
) -> ServerRiskTierChangeAlertResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)

    # Latest score per server in the window (one row per server)
    latest_sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    latest_rows = (
        db.query(McpLlmAxisScore)
        .join(
            latest_sub,
            (McpLlmAxisScore.server_id == latest_sub.c.server_id)
            & (McpLlmAxisScore.scored_at == latest_sub.c.latest_at),
        )
        .filter(McpLlmAxisScore.axis_name == "overall_risk")
        .all()
    )

    if not latest_rows:
        return ServerRiskTierChangeAlertResponse(
            period_days=period_days,
            alert_count=0,
            alert_severity="none",
            servers=[],
            as_of=datetime.now(timezone.utc).isoformat(),
        )

    server_ids = [r.server_id for r in latest_rows]

    # Fetch server names
    name_rows = (
        db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name).where(
                McpServerRegistry.server_id.in_(server_ids)
            )
        ).all()
    )
    name_map = {r.server_id: r.name for r in name_rows}

    # For each server: get the previous label before the latest one
    changes: List[TierChangeDetail] = []
    for latest in latest_rows:
        # Previous overall_risk score for this server
        prev_rows = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == latest.server_id)
            .filter(McpLlmAxisScore.axis_name == "overall_risk")
            .filter(McpLlmAxisScore.scored_at < latest.scored_at)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
            .all()
        )

        if not prev_rows:
            continue

        prev_label = _normalize_label(prev_rows[0].label)
        curr_label = _normalize_label(latest.label)

        if prev_label == curr_label:
            continue

        direction = _change_direction(prev_label, curr_label)
        changes.append(
            TierChangeDetail(
                server_id=latest.server_id,
                server_name=name_map.get(latest.server_id),
                old_tier=prev_label,
                new_tier=curr_label,
                direction=direction,
                scored_at=latest.scored_at.isoformat() if latest.scored_at else "",
            )
        )

    return ServerRiskTierChangeAlertResponse(
        period_days=period_days,
        alert_count=len(changes),
        alert_severity=_overall_severity(changes),
        servers=changes,
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
        # Servers
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
        sess.add(McpServerRegistry(
            server_id="srv-d", name="Delta", risk_tier="CRITICAL",
            registry_source="test", url="http://d", description="d",
        ))

        # srv-a: escalation LOW -> HIGH (within window)
        sess.add(McpLlmAxisScore(
            id=1, server_id="srv-a", axis_name="overall_risk", label="LOW",
            model_version="v1a", p_top=0.15,
            scored_at=now - timedelta(days=4),
        ))
        sess.add(McpLlmAxisScore(
            id=2, server_id="srv-a", axis_name="overall_risk", label="HIGH",
            model_version="v1b", p_top=0.75,
            scored_at=now - timedelta(days=1),
        ))

        # srv-b: de-escalation HIGH -> LOW (within window)
        sess.add(McpLlmAxisScore(
            id=3, server_id="srv-b", axis_name="overall_risk", label="HIGH",
            model_version="v1a", p_top=0.80,
            scored_at=now - timedelta(days=3),
        ))
        sess.add(McpLlmAxisScore(
            id=10, server_id="srv-b", axis_name="overall_risk", label="LOW",
            model_version="v1b", p_top=0.20,
            scored_at=now - timedelta(days=1),
        ))

        # srv-c: lateral change MEDIUM -> MEDIUM (no tier change — excluded)
        sess.add(McpLlmAxisScore(
            id=5, server_id="srv-c", axis_name="overall_risk", label="MEDIUM",
            model_version="v1a", p_top=0.50,
            scored_at=now - timedelta(days=5),
        ))
        sess.add(McpLlmAxisScore(
            id=11, server_id="srv-c", axis_name="overall_risk", label="MEDIUM",
            model_version="v1b", p_top=0.52,
            scored_at=now - timedelta(days=1),
        ))

        # srv-d: escalation LOW -> CRITICAL (within window)
        sess.add(McpLlmAxisScore(
            id=7, server_id="srv-d", axis_name="overall_risk", label="LOW",
            model_version="v1a", p_top=0.10,
            scored_at=now - timedelta(days=6),
        ))
        sess.add(McpLlmAxisScore(
            id=12, server_id="srv-d", axis_name="overall_risk", label="CRITICAL",
            model_version="v1b", p_top=0.95,
            scored_at=now - timedelta(days=1),
        ))

        # srv-a: score OUTSIDE the window (should be ignored)
        sess.add(McpLlmAxisScore(
            id=9, server_id="srv-a", axis_name="overall_risk", label="MINIMAL",
            model_version="v1c", p_top=0.05,
            scored_at=now - timedelta(days=14),
        ))

        sess.commit()

    c = TestClient(app)

    # Test 1: returns alerts
    r = c.get("/api/alerts/server-risk-tier-change?period_days=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alert_count"] == 3, f"expected 3 alerts, got {body}"
    assert body["alert_severity"] == "critical", f"expected critical, got {body['alert_severity']}"
    assert len(body["servers"]) == 3

    # Test 2: srv-a escalation
    a = next((s for s in body["servers"] if s["server_id"] == "srv-a"), None)
    assert a is not None
    assert a["old_tier"] == "LOW"
    assert a["new_tier"] == "HIGH"
    assert a["direction"] == "escalation"

    # Test 3: srv-b de-escalation
    b = next((s for s in body["servers"] if s["server_id"] == "srv-b"), None)
    assert b is not None
    assert b["direction"] == "de_escalation"

    # Test 4: srv-d critical escalation
    d = next((s for s in body["servers"] if s["server_id"] == "srv-d"), None)
    assert d is not None
    assert d["new_tier"] == "CRITICAL"
    assert d["direction"] == "escalation"

    # Test 5: srv-c lateral should NOT appear (same label)
    c_srv = next((s for s in body["servers"] if s["server_id"] == "srv-c"), None)
    assert c_srv is None, "srv-c lateral (MEDIUM->MEDIUM) should not appear"

    # Test 6: empty window
    r2 = c.get("/api/alerts/server-risk-tier-change?period_days=1")
    assert r2.status_code == 200
    assert r2.json()["alert_count"] == 0
    assert r2.json()["alert_severity"] == "none"

    print("PASS")
    sys.exit(0)
