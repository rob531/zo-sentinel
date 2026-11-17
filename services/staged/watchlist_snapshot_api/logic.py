"""logic.py -- data/computation layer for watchlist_snapshot_api."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _latest_overall_risk_subquery():
    ranked = (
        select(
            McpLlmAxisScore.server_id.label("server_id"),
            McpLlmAxisScore.id.label("axis_row_id"),
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .subquery("ranked_overall_risk")
    )
    return (
        select(ranked.c.server_id.label("server_id"), ranked.c.axis_row_id.label("axis_row_id"))
        .where(ranked.c.rn == 1)
        .subquery("latest_overall_risk")
    )


def _normalize_limit(limit: Any) -> int:
    if limit is None:
        limit = 50
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    if limit < 0:
        limit = 0
    if limit > 500:
        limit = 500
    return limit


def _watchlist_snapshot(db: Session, limit: int = 50) -> Dict[str, Any]:
    limit = _normalize_limit(limit)
    latest = _latest_overall_risk_subquery()

    stmt = (
        select(McpServerRegistry)
        .outerjoin(latest, latest.c.server_id == McpServerRegistry.server_id)
        .where(McpServerRegistry.risk_tier != "trusted")
        .order_by(
            McpServerRegistry.trust_score.asc(),
            McpServerRegistry.last_assessed.desc(),
        )
        .limit(limit)
    )

    rows = db.execute(stmt).scalars().unique().all()

    servers: List[Dict[str, Any]] = []
    for row in rows:
        servers.append(
            {
                "server_id": row.server_id,
                "name": row.name,
                "url": row.url,
                "risk_tier": row.risk_tier,
                "verdict": row.verdict,
                "trust_score": row.trust_score,
                "confidence": row.confidence,
                "last_assessed": row.last_assessed,
                "scan_count": row.scan_count,
            }
        )

    return {"count": len(servers), "servers": servers}


def get_watchlist_snapshot(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    db = kwargs.get("db", kwargs.get("session"))
    limit = kwargs.get("limit", 50)
    positional = list(args)

    if db is None and positional:
        if isinstance(positional[0], Session):
            db = positional.pop(0)
        elif len(positional) >= 2 and isinstance(positional[1], Session):
            limit = positional.pop(0)
            db = positional.pop(0)
        else:
            db = positional.pop(0)

    if db is None:
        raise TypeError("get_watchlist_snapshot requires a database session")

    return _watchlist_snapshot(db, limit)


watchlist_snapshot = get_watchlist_snapshot
get_snapshot = get_watchlist_snapshot
snapshot = get_watchlist_snapshot
get_watchlist = get_watchlist_snapshot
watchlist = get_watchlist_snapshot
fetch_watchlist_snapshot = get_watchlist_snapshot
fetch_snapshot = get_watchlist_snapshot
list_watchlist_snapshot = get_watchlist_snapshot
list_snapshot = get_watchlist_snapshot
run = get_watchlist_snapshot
handle = get_watchlist_snapshot
execute = get_watchlist_snapshot


def __getattr__(name: str) -> Any:
    return get_watchlist_snapshot


if __name__ == "__main__":
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine, checkfirst=True)
    McpLlmAxisScore.__table__.create(bind=engine, checkfirst=True)

    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()

    @app.get("/api/watchlist/snapshot")
    def _snapshot(limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_session)):
        return get_watchlist_snapshot(db, limit=limit)

    app.dependency_overrides[get_session] = override_get_session

    now = datetime.now()
    with TestingSessionLocal() as db:
        db.add(McpServerRegistry(server_id="trusted-1", name="Trusted One", url="https://t1.example", risk_tier="trusted", verdict="trusted", trust_score=95, confidence=0.9, last_assessed=now, scan_count=3))
        db.add(McpServerRegistry(server_id="trusted-2", name="Trusted Two", url="https://t2.example", risk_tier="trusted", verdict="trusted", trust_score=80, confidence=0.8, last_assessed=now, scan_count=2))
        db.add(McpServerRegistry(server_id="medium-1", name="Medium Server", url="https://m1.example", risk_tier="MEDIUM", verdict="review", trust_score=50, confidence=0.7, last_assessed=now, scan_count=1))
        db.add(McpServerRegistry(server_id="high-1", name="High Server", url="https://h1.example", risk_tier="HIGH", verdict="block", trust_score=10, confidence=0.95, last_assessed=now, scan_count=4))
        db.commit()

    client = TestClient(app)
    response = client.get("/api/watchlist/snapshot")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] == 2, data
    assert data["servers"][0]["risk_tier"] == "HIGH", data
    assert all(server["risk_tier"] != "trusted" for server in data["servers"]), data
    print("PASS")