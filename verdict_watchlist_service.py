"""
Server watchlist service & HTTP exemption/watchlist API (`verdict_watchlist_service.py`).

Preserves the in-process `VerdictWatchlistService` class and adds an authenticated
FastAPI `router` persisted in the existing `perspectives` table (zero schema changes)
so users and admins can manage monitored servers and time-bounded risk waivers.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Perspective
from clerk_m2m_auth import Principal, get_principal

router = APIRouter(tags=["watchlist"])
_WATCH_PREFIX = "wl_"


# --------------------------------------------------------------------------- #
# Simple in-process persistence layer (write_service)
# --------------------------------------------------------------------------- #
class WriteService:
    """Minimal key/value store used to persist the in-process watchlist."""

    def __init__(self):
        self._store: Dict[str, Any] = {}

    def write(self, key: str, value: Any) -> None:
        self._store[key] = value

    def read(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)


_notification_queue: List[Dict[str, Any]] = []


def queue_notification(org_id: str, user_id: str, server_id: str,
                       old_tier: str, new_tier: str) -> None:
    _notification_queue.append({
        "org_id": org_id,
        "user_id": user_id,
        "server_id": server_id,
        "old_tier": old_tier,
        "new_tier": new_tier,
    })


_WATCHLIST_KEY = "verdict_watchlist"


@dataclass(frozen=True)
class Watcher:
    org_id: str
    user_id: str


class VerdictWatchlistService:
    """Core service handling watch registration and verdict change notifications."""

    def __init__(self, write_service: WriteService):
        self._write_service = write_service
        self._watchlist: Dict[str, List[Watcher]] = self._write_service.read(
            _WATCHLIST_KEY, {}
        )

    def add_watch(self, org_id: str, user_id: str, server_id: str) -> None:
        watcher = Watcher(org_id, user_id)
        watchers = self._watchlist.setdefault(server_id, [])
        if watcher not in watchers:
            watchers.append(watcher)
            self._persist()

    def on_verdict_change(self, server_id: str, old_tier: str, new_tier: str) -> None:
        if old_tier == new_tier:
            return
        for watcher in self._watchlist.get(server_id, []):
            queue_notification(
                org_id=watcher.org_id,
                user_id=watcher.user_id,
                server_id=server_id,
                old_tier=old_tier,
                new_tier=new_tier,
            )

    def _persist(self) -> None:
        serialisable = {
            server: [watcher.__dict__ for watcher in watchers]
            for server, watchers in self._watchlist.items()
        }
        self._write_service.write(_WATCHLIST_KEY, serialisable)

    @staticmethod
    def get_notification_queue() -> List[Dict[str, Any]]:
        return list(_notification_queue)


# --------------------------------------------------------------------------- #
# Persistent DB-backed Watchlist & Scoped Exemption HTTP Router
# --------------------------------------------------------------------------- #
_VALID_DECISIONS = ("watching", "approved", "approved_with_conditions", "waived", "blocked")


class WatchlistCreateRequest(BaseModel):
    server_id: str
    decision: str = "watching"
    condition: str = ""
    expires_at: Optional[str] = None
    reason: str = ""


@router.get("/api/watchlist")
def list_watchlist(
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Return user/org watchlist and exemption entries enriched with registry state."""
    stmt = select(Perspective).where(Perspective.id.like(f"{_WATCH_PREFIX}%"))
    if principal.role != "admin":
        stmt = stmt.where(Perspective.created_by == principal.user_id)
    rows = db.execute(stmt.order_by(Perspective.created_at.desc())).scalars().all()
    items = []
    for p in rows:
        meta = dict(p.facet_filters or {})
        sid = str(meta.get("server_id") or "")
        reg = db.get(McpServerRegistry, sid) if sid else None
        items.append({
            "id": p.id,
            "server_id": sid,
            "server_name": reg.name if reg else sid,
            "url": reg.url if reg else "",
            "risk_tier": (reg.risk_tier or "unassessed") if reg else "unknown",
            "decision": meta.get("decision", "watching"),
            "condition": meta.get("condition", ""),
            "expires_at": meta.get("expires_at"),
            "reason": meta.get("reason") or p.description,
            "created_by": p.created_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        })
    return {"items": items, "count": len(items)}


@router.post("/api/watchlist", status_code=status.HTTP_201_CREATED)
def add_watchlist_entry(
    req: WatchlistCreateRequest,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Create a watchlist entry or time-bounded waiver in `perspectives`."""
    sid = (req.server_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="server_id is required")
    decision = req.decision if req.decision in _VALID_DECISIONS else "watching"
    wid = f"{_WATCH_PREFIX}{uuid.uuid4().hex[:16]}"
    reg = db.get(McpServerRegistry, sid)
    label = f"{decision}:{(reg.name if reg else sid)}"[:255]
    entry = Perspective(
        id=wid,
        org_id=f"waiver:{principal.user_id}"[:64],
        name=label,
        description=(req.reason or "")[:1000],
        facet_filters={
            "kind": "waiver" if decision != "watching" else "watch",
            "server_id": sid,
            "decision": decision,
            "condition": (req.condition or "").strip(),
            "expires_at": req.expires_at,
            "reason": (req.reason or "").strip(),
        },
        created_by=principal.user_id[:128],
    )
    db.add(entry)
    db.commit()
    return {"status": "created", "id": wid, "server_id": sid, "decision": decision}


@router.delete("/api/watchlist/{item_id}")
def delete_watchlist_entry(
    item_id: str,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Remove a watchlist or waiver entry owned by the caller (or any if admin)."""
    entry = db.get(Perspective, item_id)
    if entry is None or not str(entry.id).startswith(_WATCH_PREFIX):
        raise HTTPException(status_code=404, detail="Watchlist entry not found")
    if principal.role != "admin" and entry.created_by != principal.user_id:
        raise HTTPException(status_code=403, detail="Not permitted to remove another user's entry")
    db.delete(entry)
    db.commit()
    return {"status": "deleted", "id": item_id}


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # 1. Existing in-process watchlist service verification
    ws = WriteService()
    svc = VerdictWatchlistService(ws)
    svc.add_watch("org-123", "user-456", "server-789")
    svc.on_verdict_change("server-789", "bronze", "silver")
    queued = VerdictWatchlistService.get_notification_queue()
    assert len(queued) == 1 and queued[0]["new_tier"] == "silver"

    # 2. HTTP router CRUD verification against SQLite
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng)
    with TS() as s:
        s.add(McpServerRegistry(server_id="srv-fs", name="server-filesystem", url="https://github.com/org/fs", risk_tier="HIGH"))
        s.commit()

    def _override_session():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_session
    test_app.dependency_overrides[get_principal] = lambda: Principal(user_id="wl-admin", role="admin")

    c = TestClient(test_app)
    r_post = c.post("/api/watchlist", json={"server_id": "srv-fs", "decision": "waived", "condition": "--read-only", "expires_at": "2027-01-01", "reason": "Scoped workspace directory"})
    assert r_post.status_code == 201, r_post.text
    wid = r_post.json()["id"]
    r_list = c.get("/api/watchlist")
    assert r_list.status_code == 200 and r_list.json()["count"] == 1
    assert r_list.json()["items"][0]["risk_tier"] == "HIGH"
    assert c.delete(f"/api/watchlist/{wid}").status_code == 200
    assert c.get("/api/watchlist").json()["count"] == 0
    print("PASS")
