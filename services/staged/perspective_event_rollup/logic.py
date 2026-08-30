from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, PerspectiveEvent


class SeriesPoint(BaseModel):
    date: str
    count: int
    upgrades: int
    downgrades: int


class ServerRollup(BaseModel):
    server_id: str
    server_name: str
    change_count: int
    last_change: str
    last_change_type: str


class RollupResponse(BaseModel):
    window_days: int
    total_events: int
    unseen_count: int
    series: list[SeriesPoint]
    by_server: list[ServerRollup]


def _classify_change(change_type: Optional[str], old_tier: Optional[str], new_tier: Optional[str]) -> str:
    ct = (change_type or "").lower()
    if "downgrad" in ct:
        return "downgrade"
    if "upgrade" in ct:
        return "upgrade"
    if old_tier is not None and new_tier is not None:
        try:
            o = float(old_tier)
            n = float(new_tier)
            if n < o:
                return "downgrade"
            if n > o:
                return "upgrade"
        except (TypeError, ValueError):
            pass
    return "other"


def rollup_events(days: int, perspective_id: Optional[str] = None, session: Optional[Session] = None) -> dict:
    """Aggregate perspective_events for the given window.

    Args:
        days: number of trailing days to include (must be >= 1).
        perspective_id: optional filter to a single perspective.
        session: optional SQLAlchemy session (used in tests); if None a fresh
            one is obtained via the FastAPI dependency.

    Returns:
        dict matching RollupResponse.
    """
    days = max(1, int(days or 1))
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    def _work(s: Session) -> dict:
        q = s.query(PerspectiveEvent).filter(PerspectiveEvent.created_at >= start)
        if perspective_id:
            q = q.filter(PerspectiveEvent.perspective_id == perspective_id)

        events = q.all()
        total_events = len(events)
        unseen_count = sum(1 for e in events if not getattr(e, "seen", False))

        # Build daily series
        bucket: dict[str, dict[str, int]] = {}
        for i in range(days):
            d = (start + timedelta(days=i)).date().isoformat()
            bucket[d] = {"count": 0, "upgrades": 0, "downgrades": 0}

        for e in events:
            ts = getattr(e, "created_at", None)
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            d_key = ts.astimezone(timezone.utc).date().isoformat()
            if d_key not in bucket:
                bucket[d_key] = {"count": 0, "upgrades": 0, "downgrades": 0}
            bucket[d_key]["count"] += 1
            klass = _classify_change(getattr(e, "change_type", None), getattr(e, "old_tier", None), getattr(e, "new_tier", None))
            if klass == "upgrade":
                bucket[d_key]["upgrades"] += 1
            elif klass == "downgrade":
                bucket[d_key]["downgrades"] += 1

        series = [
            {"date": d, "count": v["count"], "upgrades": v["upgrades"], "downgrades": v["downgrades"]}
            for d, v in sorted(bucket.items())
        ]

        # Per-server rollup
        server_stats: dict[str, dict] = {}
        for e in events:
            sid = getattr(e, "server_id", None)
            if not sid:
                continue
            entry = server_stats.setdefault(
                sid,
                {"change_count": 0, "last_change": None, "last_change_type": None},
            )
            entry["change_count"] += 1
            ts = getattr(e, "created_at", None)
            if ts is not None:
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                iso_ts = ts.astimezone(timezone.utc).isoformat()
                cur = entry["last_change"]
                if cur is None or iso_ts > cur:
                    entry["last_change"] = iso_ts
                    entry["last_change_type"] = getattr(e, "change_type", None) or "unknown"

        # Resolve server names via registry join (single batch query)
        server_ids = list(server_stats.keys())
        name_map: dict[str, str] = {}
        if server_ids:
            rows = (
                s.query(McpServerRegistry.server_id, McpServerRegistry.name)
                .filter(McpServerRegistry.server_id.in_(server_ids))
                .all()
            )
            for r in rows:
                name_map[r[0]] = r[1] or r[0]

        by_server = []
        for sid in sorted(server_stats.keys(), key=lambda x: server_stats[x]["change_count"], reverse=True):
            entry = server_stats[sid]
            by_server.append({
                "server_id": sid,
                "server_name": name_map.get(sid, sid),
                "change_count": entry["change_count"],
                "last_change": entry["last_change"] or "",
                "last_change_type": entry["last_change_type"] or "unknown",
            })

        return {
            "window_days": days,
            "total_events": total_events,
            "unseen_count": unseen_count,
            "series": series,
            "by_server": by_server,
        }

    if session is not None:
        return _work(session)

    # Path used by FastAPI: obtain a session via the real dependency.
    from app.db import SessionLocal  # type: ignore

    with SessionLocal() as s:
        return _work(s)


def register_routes(app: FastAPI) -> None:
    """Attach the rollup endpoint to the given FastAPI app."""

    @app.get("/api/perspectives/events/rollup", response_model=RollupResponse)
    def _endpoint(
        days: int = 7,
        perspective_id: Optional[str] = None,
        session: Session = Depends(get_session),
    ) -> RollupResponse:
        payload = rollup_events(days=days, perspective_id=perspective_id, session=session)
        return RollupResponse(**payload)


if __name__ == "__main__":
    # Self-test: build an isolated in-memory FastAPI app, override get_session
    # with a SQLite-backed store seeded with 3 servers and 5 events.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base  # type: ignore

    that_app = FastAPI()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = _override_get_session
    register_routes(that_app)

    from datetime import datetime as _dt

    now = _dt.now(timezone.utc)
    with TestSession() as s:
        s.add_all([
            McpServerRegistry(server_id="srv-1", name="Alpha", url="http://a", risk_tier="low"),
            McpServerRegistry(server_id="srv-2", name="Bravo", url="http://b", risk_tier="med"),
            McpServerRegistry(server_id="srv-3", name="Charlie", url="http://c", risk_tier="high"),
        ])
        s.add_all([
            PerspectiveEvent(perspective_id="p1", server_id="srv-1", change_type="upgrade",
                             old_tier="2", new_tier="3", seen=False,
                             created_at=now - timedelta(days=2)),
            PerspectiveEvent(perspective_id="p1", server_id="srv-2", change_type="downgrade",
                             old_tier="3", new_tier="1", seen=True,
                             created_at=now - timedelta(days=2)),
            PerspectiveEvent(perspective_id="p1", server_id="srv-3", change_type="upgrade",
                             old_tier="1", new_tier="2", seen=False,
                             created_at=now - timedelta(days=1, hours=6)),
            PerspectiveEvent(perspective_id="p1", server_id="srv-1", change_type="downgrade",
                             old_tier="3", new_tier="2", seen=True,
                             created_at=now - timedelta(hours=12)),
            PerspectiveEvent(perspective_id="p1", server_id="srv-2", change_type="upgrade",
                             old_tier="1", new_tier="2", seen=False,
                             created_at=now - timedelta(hours=2)),
        ])
        s.commit()

    from fastapi.testclient import TestClient
    client = TestClient(that_app)
    resp = client.get("/api/perspectives/events/rollup", params={"days": 7})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_events"] >= 5, body
    assert len(body["series"]) >= 1, body
    assert body["unseen_count"] >= 0, body
    assert body["window_days"] == 7, body
    print("PASS")