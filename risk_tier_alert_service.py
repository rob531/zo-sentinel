"""risk_tier_alert_service.py -- Risk tier threshold alert router.

Reads risk_tier from mcp_llm_axis_scores via the app DB session, compares
against configurable tier thresholds, and dispatches alerts via configured
notification channels (email / webhook).

Provides the trigger_alert() helper + notification primitives used by
server_risk_tier_alert_service.py.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Org

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])

# deps: requests

# ---------------------------------------------------------------------------
# Notification channel config
# ---------------------------------------------------------------------------
_EMAIL_WEBHOOK_URL = os.getenv("ALERT_EMAIL_WEBHOOK_URL", "")
_WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "")
_ALERT_EMAIL = os.getenv("ALERT_EMAIL_TO", "")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class AlertPayload(BaseModel):
    server_id: str
    old_tier: Optional[str]
    new_tier: str
    timestamp: datetime
    server_name: Optional[str] = None
    org_name: Optional[str] = None
    reason: str = ""


class RiskTierChangeRequest(BaseModel):
    server_id: str
    model_version: Optional[str] = None


class RiskTierAlertResponse(BaseModel):
    status: str
    triggered: bool
    payload: Optional[AlertPayload] = None


# ---------------------------------------------------------------------------
# Helpers used by server_risk_tier_alert_service.py
# ---------------------------------------------------------------------------
def get_org_name(db: Session, server_id: str) -> Optional[str]:
    """Look up the org name for a server via its registry entry."""
    reg = db.get(McpServerRegistry, server_id)
    if reg and hasattr(reg, "org_id") and reg.org_id:
        org = db.get(Org, reg.org_id)
        return org.name if org else None
    return None


def get_server_name(db: Session, server_id: str) -> Optional[str]:
    """Look up the server name from the registry."""
    reg = db.get(McpServerRegistry, server_id)
    return reg.name if reg else None


def send_email_alert(payload: AlertPayload) -> bool:
    """Dispatch an email alert. Returns True on success."""
    if not _EMAIL_WEBHOOK_URL:
        logger.info("[email] No ALERT_EMAIL_WEBHOOK_URL configured; skipping.")
        return False
    try:
        import requests
        body = {
            "to": _ALERT_EMAIL,
            "subject": f"[ZoSentinel] Risk tier change: {payload.server_id} "
                       f"({payload.old_tier or '?'} -> {payload.new_tier})",
            "body": (
                f"Server: {payload.server_name or payload.server_id}\n"
                f"Server ID: {payload.server_id}\n"
                f"Org: {payload.org_name or 'unknown'}\n"
                f"Tier change: {payload.old_tier or 'N/A'} -> {payload.new_tier}\n"
                f"Reason: {payload.reason}\n"
                f"Time: {payload.timestamp.isoformat()}"
            ),
        }
        resp = requests.post(_EMAIL_WEBHOOK_URL, json=body, timeout=10)
        resp.raise_for_status()
        logger.info(f"[email] Alert sent for {payload.server_id}: %s -> %s",
                    payload.old_tier, payload.new_tier)
        return True
    except Exception as exc:
        logger.warning("[email] Failed to send alert: %s", exc)
        return False


def send_webhook_alert(payload: AlertPayload) -> bool:
    """Dispatch a webhook alert. Returns True on success."""
    if not _WEBHOOK_URL:
        logger.info("[webhook] No ALERT_WEBHOOK_URL configured; skipping.")
        return False
    try:
        import requests
        body = {
            "event": "risk_tier_change",
            "server_id": payload.server_id,
            "server_name": payload.server_name,
            "org_name": payload.org_name,
            "old_tier": payload.old_tier,
            "new_tier": payload.new_tier,
            "reason": payload.reason,
            "timestamp": payload.timestamp.isoformat(),
        }
        resp = requests.post(_WEBHOOK_URL, json=body, timeout=10)
        resp.raise_for_status()
        logger.info("[webhook] Alert sent for %s: %s -> %s",
                    payload.server_id, payload.old_tier, payload.new_tier)
        return True
    except Exception as exc:
        logger.warning("[webhook] Failed to send alert: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Tier significance thresholds
# ---------------------------------------------------------------------------
def _load_thresholds() -> List[str]:
    """Parse ALERT_TIER_THRESHOLDS env var (comma-separated tier labels)."""
    raw = os.getenv("ALERT_TIER_THRESHOLDS", "")
    if raw:
        return [t.strip().upper() for t in raw.split(",") if t.strip()]
    # Sensible defaults: any rise to HIGH or above triggers
    return ["HIGH", "CRITICAL", "DANGER_BLOCKED", "WARNING_RESTRICTED"]


THRESHOLD_TIERS: List[str] = _load_thresholds()


def should_alert(old_tier: Optional[str], new_tier: str) -> bool:
    """Return True when new_tier crosses into the configured threshold tiers."""
    if not new_tier:
        return False
    new_upper = new_tier.upper()
    if new_upper not in THRESHOLD_TIERS:
        return False
    if not old_tier:
        return True
    old_upper = old_tier.upper()
    # Don't alert on downward (improving) transitions
    tier_rank = {t: i for i, t in enumerate(THRESHOLD_TIERS)}
    old_rank = tier_rank.get(old_upper, -1)
    new_rank = tier_rank.get(new_upper, -1)
    return new_rank > old_rank


def trigger_alert(
    db: Session,
    server_id: str,
    old_tier: Optional[str],
    new_tier: str,
    timestamp: Optional[datetime] = None,
    reason: str = "",
) -> AlertPayload:
    """Build and dispatch an AlertPayload for a tier change.
    Used directly by server_risk_tier_alert_service.py."""
    ts = timestamp or datetime.utcnow()
    server_name = get_server_name(db, server_id)
    org_name = get_org_name(db, server_id)
    payload = AlertPayload(
        server_id=server_id,
        old_tier=old_tier,
        new_tier=new_tier,
        timestamp=ts,
        server_name=server_name,
        org_name=org_name,
        reason=reason,
    )
    send_email_alert(payload)
    send_webhook_alert(payload)
    return payload


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------
def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


@router.post("/risk_tier", response_model=RiskTierAlertResponse)
def check_risk_tier_alert(
    req: RiskTierChangeRequest,
    db: Session = Depends(get_session),
) -> RiskTierAlertResponse:
    """Read the server's current overall_risk axis label from mcp_llm_axis_scores,
    compare against the previous score, and fire alerts if the threshold is crossed."""
    server_id = req.server_id.strip()
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id is required")

    mv = req.model_version or _latest_model_version(db, server_id)
    if not mv:
        raise HTTPException(
            status_code=404,
            detail=f"No scores found for server_id {server_id!r}",
        )

    # Current overall_risk row
    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == mv,
            McpLlmAxisScore.axis_name == "overall_risk",
        )
    ).scalars().all()

    current_row = rows[0] if rows else None
    new_tier = current_row.label if current_row else None

    if not new_tier:
        raise HTTPException(
            status_code=422,
            detail=f"No overall_risk label for server {server_id!r} "
                   f"at model_version {mv!r}",
        )

    # Find the immediately prior overall_risk row (any model_version)
    prev_row = db.execute(
        select(McpLlmAxisScore)
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.model_version != mv,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    old_tier: Optional[str] = prev_row.label if prev_row else None

    if not should_alert(old_tier, new_tier):
        return RiskTierAlertResponse(
            status="no_significant_change",
            triggered=False,
        )

    ts = current_row.scored_at if current_row else datetime.utcnow()
    reason = f"Risk tier crossed threshold (threshold tiers: {THRESHOLD_TIERS})"
    payload = trigger_alert(db, server_id, old_tier, new_tier, ts, reason)
    return RiskTierAlertResponse(
        status="alert_triggered",
        triggered=True,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    # Seed: two model versions for the same server to test tier-change detection
    s = TS()
    s.add(McpServerRegistry(
        server_id="srv1",
        name="Test MCP Server",
        url="https://example.com/test-mcp",
    ))
    # v1: LOW -> v2: CRITICAL (crosses threshold)
    s.add(McpLlmAxisScore(
        id=1,
        server_id="srv1",
        axis_name="overall_risk",
        label="LOW",
        model_version="v1.0",
    ))
    s.add(McpLlmAxisScore(
        id=2,
        server_id="srv1",
        axis_name="overall_risk",
        label="CRITICAL",
        model_version="v2.0",
    ))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # Happy path: tier change crosses threshold -> 200 + triggered=True
    r = c.post("/alerts/risk_tier", json={"server_id": "srv1", "model_version": "v2.0"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    j = r.json()
    assert j["triggered"] is True, f"Expected triggered=True, got {j}"
    assert j["payload"]["new_tier"] == "CRITICAL", j
    assert j["payload"]["old_tier"] == "LOW", j

    # Edge: server not found -> 404
    r2 = c.post("/alerts/risk_tier", json={"server_id": "nonexistent"})
    assert r2.status_code == 404, f"Expected 404, got {r2.status_code}"

    # Edge: no threshold cross (LOW -> MEDIUM, MEDIUM not in default thresholds)
    s = TS()
    s.add(McpServerRegistry(server_id="srv2", name="Low Risk Server",
                            url="https://example.com/low-risk"))
    s.add(McpLlmAxisScore(id=3, server_id="srv2", axis_name="overall_risk",
                          label="LOW", model_version="v1.0"))
    s.add(McpLlmAxisScore(id=4, server_id="srv2", axis_name="overall_risk",
                          label="MEDIUM", model_version="v2.0"))
    s.commit()
    s.close()
    r3 = c.post("/alerts/risk_tier", json={"server_id": "srv2", "model_version": "v2.0"})
    assert r3.status_code == 200, r3.text
    j3 = r3.json()
    assert j3["triggered"] is False, f"Expected triggered=False for non-threshold cross, got {j3}"

    print("PASS")
