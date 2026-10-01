# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Escalation Consumer.

Detects servers whose risk tier has degraded by comparing McpLlmAxisScore
rows with escalated=True against McpServerRegistry.risk_tier. Writes
PerspectiveEvent rows for tier transitions and queries audit_log via
write_service.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Annotated, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["risk_tier_escalation_consumer"])

# Tier order for degradation detection
TIER_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _tier_level(tier: Optional[str]) -> int:
    if tier is None:
        return 0
    return TIER_ORDER.get(tier.lower(), 0)


def _write_service(path: str, payload: dict) -> dict:
    url = f"http://127.0.0.1:8772{path}"
    resp = requests.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    status: str
    service: str = "risk_tier_escalation_consumer"
    last_run: Optional[datetime] = None


class ConsumeResponse(BaseModel):
    consumed: int
    escalations_detected: int
    transitions_recorded: int


class EscalationEventOut(BaseModel):
    id: int
    server_id: str
    server_name: Optional[str]
    previous_tier: Optional[str]
    new_tier: Optional[str]
    escalation_reason: str
    triggered_at: datetime
    acknowledged: bool


class AuditLogEntry(BaseModel):
    id: int
    event_type: str
    server_id: str
    details: str
    created_at: datetime


class TierTransitionInput(BaseModel):
    server_id: str
    previous_tier: Optional[str] = None
    new_tier: str
    trigger_event: str


class TierTransitionResponse(BaseModel):
    escalation_created: bool
    escalation_id: Optional[int] = None
    audit_log_id: Optional[str] = None
    message: str


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _determine_reason(previous_tier: Optional[str], new_tier: Optional[str]) -> str:
    if previous_tier is None:
        return f"Initial tier assignment: {new_tier}"
    prev_level = _tier_level(previous_tier)
    new_level = _tier_level(new_tier)
    if new_level > prev_level:
        return f"Risk tier degradation: {previous_tier} -> {new_tier}"
    if new_level < prev_level:
        return f"Risk tier improvement: {previous_tier} -> {new_tier}"
    return f"No change in risk tier: {previous_tier} -> {new_tier}"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/risk-tier-escalation/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse(status="ok")


@router.post("/risk-tier-escalation/consume", response_model=ConsumeResponse)
async def consume_escalations(
    db: Session = Depends(get_session),
) -> ConsumeResponse:
    """Poll for newly escalated servers and record tier transitions.

    Queries McpLlmAxisScore for escalated=True rows, compares against
    McpServerRegistry.risk_tier, and writes PerspectiveEvent rows for
    any degradation. Also writes audit entries via write_service.
    """
    escalated_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.escalated == True)
        .all()
    )

    escalations_detected = 0
    transitions_recorded = 0
    seen_servers: set[str] = set()

    for row in escalated_rows:
        if row.server_id in seen_servers:
            continue
        seen_servers.add(row.server_id)

        server = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == row.server_id)
            .first()
        )
        if not server:
            continue

        current_tier: Optional[str] = getattr(server, "risk_tier", None)
        new_tier: Optional[str] = getattr(row, "escalated_to", None) or current_tier

        if current_tier is None:
            continue

        if _tier_level(new_tier) > _tier_level(current_tier):
            escalations_detected += 1

            event = PerspectiveEvent(
                perspective_id="risk_tier_escalation_consumer",
                server_id=row.server_id,
                change_type="tier_changed",
                old_tier=current_tier,
                new_tier=new_tier,
                seen=False,
            )
            db.add(event)
            transitions_recorded += 1

            # Write audit log via write_service (non-fatal if unavailable)
            try:
                _write_service("/write", {
                    "table": "audit_log",
                    "rows": {
                        "event_type": "RISK_TIER_ESCALATION",
                        "target_server_id": row.server_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "meta": {
                            "previous_tier": current_tier,
                            "new_tier": new_tier,
                            "axis_name": getattr(row, "axis_name", None),
                            "label": getattr(row, "label", None),
                        },
                    },
                })
            except Exception:
                pass

    if escalated_rows:
        db.commit()

    return ConsumeResponse(
        consumed=len(escalated_rows),
        escalations_detected=escalations_detected,
        transitions_recorded=transitions_recorded,
    )


@router.post("/risk-tier-escalation/tier-transition", response_model=TierTransitionResponse)
async def process_tier_transition(
    input_data: TierTransitionInput,
    db: Session = Depends(get_session),
) -> TierTransitionResponse:
    """Manually process a risk tier transition for a server."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == input_data.server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {input_data.server_id} not found")

    escalation_reason = _determine_reason(input_data.previous_tier, input_data.new_tier)
    escalation_id: Optional[int] = None
    audit_log_id: Optional[str] = None
    escalation_created = False

    if input_data.previous_tier is not None:
        event = PerspectiveEvent(
            perspective_id="risk_tier_escalation_consumer",
            server_id=input_data.server_id,
            change_type="tier_changed",
            old_tier=input_data.previous_tier,
            new_tier=input_data.new_tier,
            seen=False,
        )
        db.add(event)
        db.flush()
        escalation_id = event.id
        escalation_created = True

    try:
        result = _write_service("/write", {
            "table": "audit_log",
            "rows": {
                "event_type": "RISK_TIER_TRANSITION",
                "target_server_id": input_data.server_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "meta": {
                    "previous_tier": input_data.previous_tier,
                    "new_tier": input_data.new_tier,
                    "trigger_event": input_data.trigger_event,
                    "escalation_reason": escalation_reason,
                },
            },
        })
        audit_log_id = result.get("id") if isinstance(result, dict) else None
    except Exception:
        pass

    db.commit()

    return TierTransitionResponse(
        escalation_created=escalation_created,
        escalation_id=escalation_id,
        audit_log_id=audit_log_id,
        message=escalation_reason,
    )


@router.get(
    "/risk-tier-escalation/escalations",
    response_model=list[EscalationEventOut],
)
async def list_escalations(
    server_id: Annotated[Optional[str], Query(description="Filter by server_id")] = None,
    acknowledged: Annotated[Optional[bool], Query(description="Filter by acknowledgement state")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    db: Session = Depends(get_session),
) -> list[EscalationEventOut]:
    """Return recorded tier-transition events from perspective_events."""
    q = db.query(PerspectiveEvent).filter(
        PerspectiveEvent.perspective_id == "risk_tier_escalation_consumer"
    )
    if server_id is not None:
        q = q.filter(PerspectiveEvent.server_id == server_id)
    if acknowledged is not None:
        q = q.filter(PerspectiveEvent.seen == (not acknowledged))

    rows = q.order_by(PerspectiveEvent.created_at.desc()).limit(limit).all()

    out = []
    for row in rows:
        server = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == row.server_id)
            .first()
        )
        server_name = getattr(server, "name", None) if server else None

        reason = _determine_reason(row.old_tier, row.new_tier)
        out.append(
            EscalationEventOut(
                id=row.id,
                server_id=row.server_id,
                server_name=server_name,
                previous_tier=row.old_tier,
                new_tier=row.new_tier,
                escalation_reason=reason,
                triggered_at=row.created_at,
                acknowledged=row.seen,
            )
        )
    return out


@router.patch(
    "/risk-tier-escalation/escalations/{escalation_id}/acknowledge",
)
async def acknowledge_escalation(
    escalation_id: int,
    db: Session = Depends(get_session),
) -> dict:
    """Mark a perspective event as seen (acknowledged)."""
    event = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.id == escalation_id)
        .first()
    )
    if not event:
        raise HTTPException(status_code=404, detail="Escalation not found")
    event.seen = True
    db.commit()
    return {"status": "acknowledged", "id": escalation_id}


@router.get(
    "/risk-tier-escalation/audit-log",
    response_model=list[AuditLogEntry],
)
async def get_audit_log(
    event_type: Annotated[Optional[str], Query(description="Filter by event_type")] = None,
    server_id: Annotated[Optional[str], Query(description="Filter by server_id")] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    db: Session = Depends(get_session),
) -> list[AuditLogEntry]:
    """Query audit log entries via write_service."""
    params: dict = {"sql": "SELECT * FROM audit_log WHERE 1=1"}
    conditions: list[str] = []
    if event_type:
        conditions.append("event_type = :event_type")
        params["event_type"] = event_type
    if server_id:
        conditions.append("target_server_id = :server_id")
        params["server_id"] = server_id
    if conditions:
        params["sql"] += " AND " + " AND ".join(conditions)
    params["sql"] += " ORDER BY timestamp DESC LIMIT :limit"
    params["limit"] = limit

    try:
        result = _write_service("/query", params)
        rows = result.get("rows", []) if isinstance(result, dict) else []
    except Exception:
        rows = []

    return [
        AuditLogEntry(
            id=i,
            event_type=r.get("event_type", ""),
            server_id=r.get("target_server_id", ""),
            details=r.get("meta", {}).get("escalation_reason", "")
            if isinstance(r.get("meta"), dict)
            else "",
            created_at=r.get("timestamp", ""),
        )
        for i, r in enumerate(rows)
    ]


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _run_self_test()


def _run_self_test() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session as _real_get_session
    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[_real_get_session] = _override

    client = TestClient(app)

    # Seed test data
    db = TestSession()
    db.add(McpServerRegistry(
        server_id="srv-escal-001",
        name="Escalation Test Server",
        risk_tier="low",
        registry_source="test",
    ))
    db.add(McpLlmAxisScore(
        server_id="srv-escal-001",
        axis_name="overall_risk",
        label="HIGH",
        label_index=0,
        p_top=0.85,
        escalated=True,
        escalated_to="high",
        model_version="v1",
        decision_rule_version="r1",
        scored_at=datetime.now(timezone.utc),
    ))
    db.commit()
    db.close()

    # Happy path: health
    r = client.get("/api/risk-tier-escalation/health")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # Happy path: consume detects escalation
    r = client.post("/api/risk-tier-escalation/consume")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["consumed"] >= 1
    assert body["escalations_detected"] >= 1

    # Escalations listed
    r = client.get("/api/risk-tier-escalation/escalations")
    assert r.status_code == 200, r.text
    escalations = r.json()
    assert len(escalations) >= 1
    esc = escalations[0]
    assert esc["previous_tier"] == "low"
    assert esc["new_tier"] == "high"
    assert "degradation" in esc["escalation_reason"].lower()

    # Acknowledge
    r = client.patch(f"/api/risk-tier-escalation/escalations/{esc['id']}/acknowledge")
    assert r.status_code == 200, r.text

    # 404 on unknown escalation
    r = client.patch("/api/risk-tier-escalation/escalations/99999/acknowledge")
    assert r.status_code == 404, r.text

    # Manual tier transition
    r = client.post("/api/risk-tier-escalation/tier-transition", json={
        "server_id": "srv-escal-001",
        "previous_tier": "low",
        "new_tier": "critical",
        "trigger_event": "manual_test",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["escalation_created"] is True
    assert data["message"] == "Risk tier degradation: low -> critical"

    # 404 on unknown server for manual transition
    r = client.post("/api/risk-tier-escalation/tier-transition", json={
        "server_id": "unknown-server",
        "new_tier": "high",
        "trigger_event": "test",
    })
    assert r.status_code == 404, r.text

    # audit-log endpoint (may be empty but must not 5xx)
    r = client.get("/api/risk-tier-escalation/audit-log")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)

    return True


if __name__ == "__main__":
    try:
        ok = _run_self_test()
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)
    if ok:
        print("PASS")
        sys.exit(0)
