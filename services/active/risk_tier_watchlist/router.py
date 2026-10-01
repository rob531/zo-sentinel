# deps: fastapi, pydantic, sqlalchemy, requests
"""services.active.risk_tier_watchlist.router

GET  /api/risk-tier-watchlist            -- list high-risk watchlist entries
POST /api/risk-tier-watchlist            -- add a server to the watchlist (marks seen=false)
DELETE /api/risk-tier-watchlist/{server_id} -- remove a server from the watchlist
GET  /api/risk-tier-watchlist/events     -- escalation event history (PerspectiveEvent feed)

Auth=public, no org_id scoping (mcp_server_registry + PerspectiveEvent are global).
Uses write_service (:8772) for heartbeat and perspective_events; reads from app DB.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Perspective, PerspectiveEvent

router = APIRouter(prefix="/api/risk-tier-watchlist", tags=["risk_tier_watchlist"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HIGH_RISK_TIERS = ("HIGH_RISK_ISOLATED", "CAUTION_LIMITED", "KNOWN_THREAT")
PERSPECTIVE_NAME = "risk_tier_watchlist"
HEARTBEAT_INTERVAL = 60


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class WatchlistEntry(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    risk_tier: Optional[str] = None
    last_assessed: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None

    class Config:
        from_attributes = True


class WatchlistResponse(BaseModel):
    total: int
    entries: List[WatchlistEntry]


class AddToWatchlistRequest(BaseModel):
    server_id: str
    reason: Optional[str] = None


class AddToWatchlistResponse(BaseModel):
    ok: bool
    server_id: str
    message: str


class EscalationEvent(BaseModel):
    id: int
    perspective_id: int
    server_id: str
    change_type: Optional[str]
    old_tier: Optional[str]
    new_tier: Optional[str]
    seen: bool
    created_at: str

    class Config:
        from_attributes = True


class EscalationEventsResponse(BaseModel):
    total: int
    events: List[EscalationEvent]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get_or_create_perspective(session: Session) -> Perspective:
    p = session.query(Perspective).filter(Perspective.name == PERSPECTIVE_NAME).first()
    if p is None:
        p = Perspective(
            name=PERSPECTIVE_NAME,
            description="High-risk server escalation watchlist",
            org_id=1,
            created_by=1,
        )
        session.add(p)
        session.commit()
        session.refresh(p)
    return p


def _post_to_bus(table: str, row: dict) -> None:
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "row": row},
            timeout=5,
        )
    except requests.RequestException:
        pass


def _post_heartbeat(run_id: str) -> None:
    _post_to_bus("service_health", {
        "service": "risk_tier_watchlist",
        "run_id": run_id,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
    })


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("", response_model=WatchlistResponse)
def list_watchlist(
    tier: Optional[str] = Query(None, description="Filter by risk_tier (e.g. HIGH_RISK_ISOLATED)"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> WatchlistResponse:
    """
    List servers currently in the high-risk watchlist.
    Returns all servers whose risk_tier is in HIGH_RISK_TIERS.
    """
    q = db.query(McpServerRegistry).filter(
        McpServerRegistry.risk_tier.in_(HIGH_RISK_TIERS)
    )
    if tier:
        q = q.filter(McpServerRegistry.risk_tier == tier)
    total = q.count()
    rows = q.order_by(McpServerRegistry.last_assessed.desc()).limit(limit).all()

    entries = [
        WatchlistEntry(
            server_id=r.server_id,
            name=r.name,
            url=r.url,
            risk_tier=r.risk_tier,
            last_assessed=r.last_assessed.isoformat() if r.last_assessed else None,
            verdict=r.verdict,
            confidence=r.confidence,
        )
        for r in rows
    ]
    return WatchlistResponse(total=total, entries=entries)


@router.get("/events", response_model=EscalationEventsResponse)
def list_escalation_events(
    perspective_id: Optional[int] = Query(None, description="Filter by perspective_id"),
    unseen_only: bool = Query(False, description="Return only unseen events"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_session),
) -> EscalationEventsResponse:
    """
    Return recent PerspectiveEvent rows for this watchlist, ordered newest first.
    """
    perspective = _get_or_create_perspective(db)
    pid = perspective_id if perspective_id is not None else perspective.id

    q = db.query(PerspectiveEvent).filter(PerspectiveEvent.perspective_id == pid)
    if unseen_only:
        q = q.filter(PerspectiveEvent.seen == False)  # noqa: E712
    total = q.count()
    rows = q.order_by(PerspectiveEvent.created_at.desc()).limit(limit).all()

    events = [
        EscalationEvent(
            id=r.id,
            perspective_id=r.perspective_id,
            server_id=r.server_id,
            change_type=r.change_type,
            old_tier=r.old_tier,
            new_tier=r.new_tier,
            seen=r.seen,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]
    return EscalationEventsResponse(total=total, events=events)


@router.post("", response_model=AddToWatchlistResponse, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    req: AddToWatchlistRequest,
    db: Session = Depends(get_session),
) -> AddToWatchlistResponse:
    """
    Manually add a server to the watchlist by emitting a perspective_event.
    The server must already exist in mcp_server_registry.
    """
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == req.server_id
    ).first()

    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{req.server_id}' not found in registry.",
        )

    perspective = _get_or_create_perspective(db)
    old_tier = server.risk_tier or "UNKNOWN"

    event = PerspectiveEvent(
        perspective_id=perspective.id,
        server_id=server.server_id,
        change_type="manual_watchlist_add",
        old_tier=old_tier,
        new_tier=server.risk_tier,
        seen=False,
        created_at=datetime.now(timezone.utc),
    )
    db.add(event)
    db.commit()

    _post_to_bus("perspective_events", {
        "perspective_id": perspective.id,
        "server_id": server.server_id,
        "change_type": "manual_watchlist_add",
        "old_tier": old_tier,
        "new_tier": server.risk_tier,
        "seen": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    return AddToWatchlistResponse(
        ok=True,
        server_id=server.server_id,
        message=f"Server '{server.name}' added to watchlist.",
    )


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_from_watchlist(
    server_id: str,
    db: Session = Depends(get_session),
) -> None:
    """
    Remove a server from the watchlist by marking all its unseen events as seen.
    Does NOT delete the server from mcp_server_registry.
    """
    perspective = _get_or_create_perspective(db)
    unseen = (
        db.query(PerspectiveEvent)
        .filter(
            PerspectiveEvent.perspective_id == perspective.id,
            PerspectiveEvent.server_id == server_id,
            PerspectiveEvent.seen == False,  # noqa: E712
        )
        .all()
    )
    for ev in unseen:
        ev.seen = True
    db.commit()


# --------------------------------------------------------------------------- #
# Daemon: background escalation polling loop + heartbeat
# --------------------------------------------------------------------------- #
def _daemon_cycle(db: Session) -> int:
    """
    Poll for servers newly escalated to high-risk tiers since last check.
    Emit perspective_events for any new high-risk servers.
    Returns number of events posted.
    """
    _post_heartbeat(str(uuid.uuid4()))

    perspective = _get_or_create_perspective(db)

    servers = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.in_(HIGH_RISK_TIERS))
        .all()
    )

    seen_ids = {r.server_id for r in servers}
    events_posted = 0

    for server in servers:
        # Check if we already have a watchlist event for this server
        existing = (
            db.query(PerspectiveEvent)
            .filter(
                PerspectiveEvent.perspective_id == perspective.id,
                PerspectiveEvent.server_id == server.server_id,
                PerspectiveEvent.change_type.in_(
                    ["risk_tier_escalation", "manual_watchlist_add"]
                ),
            )
            .first()
        )
        if existing is None:
            ev = PerspectiveEvent(
                perspective_id=perspective.id,
                server_id=server.server_id,
                change_type="risk_tier_escalation",
                old_tier="UNKNOWN",
                new_tier=server.risk_tier,
                seen=False,
                created_at=datetime.now(timezone.utc),
            )
            db.add(ev)
            events_posted += 1

            _post_to_bus("perspective_events", {
                "perspective_id": perspective.id,
                "server_id": server.server_id,
                "change_type": "risk_tier_escalation",
                "old_tier": "UNKNOWN",
                "new_tier": server.risk_tier,
                "seen": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })

    if events_posted > 0:
        db.commit()

    return events_posted


def run_daemon() -> None:
    """Background daemon: polls every 5 minutes, heartbeats every 60s."""
    run_id = str(uuid.uuid4())
    stop_event = threading.Event()

    def heartbeat() -> None:
        while not stop_event.wait(HEARTBEAT_INTERVAL):
            try:
                _post_heartbeat(run_id)
            except Exception:
                pass

    def main_loop() -> None:
        while True:
            if stop_event.wait(300):
                break
            try:
                session = next(get_session())
                try:
                    _daemon_cycle(session)
                finally:
                    session.close()
            except Exception:
                pass

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    main_loop()


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    now = datetime.now(timezone.utc)

    # Seed data
    with SessionLocal() as s:
        s.add(McpServerRegistry(
            server_id="srv-high",
            name="RiskyServer",
            risk_tier="HIGH_RISK_ISOLATED",
            url="https://example.com",
            last_assessed=now,
        ))
        s.add(McpServerRegistry(
            server_id="srv-trusted",
            name="TrustedServer",
            risk_tier="TRUSTED",
            url="https://github.com/test",
            last_assessed=now,
        ))
        s.add(Perspective(
            id=1,
            name="risk_tier_watchlist",
            description="Test perspective",
            org_id=1,
            created_by=1,
        ))
        s.add(PerspectiveEvent(
            perspective_id=1,
            server_id="srv-high",
            change_type="risk_tier_escalation",
            old_tier="UNKNOWN",
            new_tier="HIGH_RISK_ISOLATED",
            seen=False,
            created_at=now,
        ))
        s.commit()

    def _override():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Happy path: list watchlist
    resp = client.get("/api/risk-tier-watchlist")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "entries" in body
    assert "total" in body
    assert body["total"] >= 1, f"Expected >=1, got {body['total']}"
    # srv-trusted should NOT be in the list
    assert not any(e["server_id"] == "srv-trusted" for e in body["entries"])

    # Filter by tier
    resp2 = client.get("/api/risk-tier-watchlist?tier=HIGH_RISK_ISOLATED")
    assert resp2.status_code == 200
    assert all(e["risk_tier"] == "HIGH_RISK_ISOLATED" for e in resp2.json()["entries"])

    # List escalation events
    resp3 = client.get("/api/risk-tier-watchlist/events")
    assert resp3.status_code == 200
    body3 = resp3.json()
    assert "events" in body3
    assert body3["total"] >= 1

    # unseen_only
    resp4 = client.get("/api/risk-tier-watchlist/events?unseen_only=true")
    assert resp4.status_code == 200
    assert all(not e["seen"] for e in resp4.json()["events"])

    # Add to watchlist (known server)
    resp5 = client.post("/api/risk-tier-watchlist", json={"server_id": "srv-trusted"})
    assert resp5.status_code == 201
    assert resp5.json()["ok"] is True

    # Add to watchlist (unknown server) -> 404
    resp6 = client.post("/api/risk-tier-watchlist", json={"server_id": "nonexistent"})
    assert resp6.status_code == 404

    # Remove from watchlist
    resp7 = client.delete("/api/risk-tier-watchlist/srv-high")
    assert resp7.status_code == 204

    print("PASS")
