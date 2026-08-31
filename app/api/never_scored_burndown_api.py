"""Never Scored Burndown API.
Provides GET /reporting/never-scored-burndown returning count of servers that have never received a scoring
and a daily burndown series over the last N days (default 30).
Mirrors the structure of verdict_breakdown_api.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Dict

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["never_scored_burndown"])

class BurndownEntry(BaseModel):
    date: str  # YYYY-MM-DD
    never_scored_count: int
    newly_scored: int

class NeverScoredBurndownResponse(BaseModel):
    as_of: str  # ISO8601 timestamp
    never_scored_count: int
    total_registry_count: int
    burndown_series: List[BurndownEntry]

def _latest_model_version(db: Session) -> str | None:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row

@router.get("/reporting/never-scored-burndown", response_model=NeverScoredBurndownResponse)
def get_never_scored_burndown(days: int = Query(30, ge=1), db: Session = Depends(get_session)) -> NeverScoredBurndownResponse:
    """Return how many servers have never been scored and a burndown series.
    """
    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days)).date()

    # Total servers in registry
    total_servers: int = db.execute(select(func.count(McpServerRegistry.server_id))).scalar_one() or 0

    # Servers that have any score (any model version)
    scored_subq = select(distinct(McpLlmAxisScore.server_id)).subquery()
    never_scored_count: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
        .where(~McpServerRegistry.server_id.in_(scored_subq))
    ).scalar_one() or 0

    # Prepare burndown series
    series: List[BurndownEntry] = []
    # Precompute first_score_date per server (if any)
    first_score_cte = (
        select(
            McpLlmAxisScore.server_id,
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at")
        )
        .group_by(McpLlmAxisScore.server_id)
        .cte("first_score")
    )
    # Map server -> first scored date (date part)
    first_score_map: Dict[str, datetime] = {}
    rows = db.execute(select(first_score_cte.c.server_id, first_score_cte.c.first_scored_at)).all()
    for sid, ts in rows:
        if ts is not None:
            first_score_map[sid] = ts

    # Compute daily counts -- strip tzinfo so we compare naive datetimes consistently
    # (SQLite stores scored_at without timezone; day boundaries are naive midnight in UTC)
    for offset in range(days + 1):
        day = start_date + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time())  # naive UTC midnight
        day_end = day_start + timedelta(days=1)
        # Servers never scored up to end of this day
        never_cnt = db.execute(
            select(func.count(McpServerRegistry.server_id))
            .where(~McpServerRegistry.server_id.in_(
                select(McpLlmAxisScore.server_id).where(McpLlmAxisScore.scored_at <= day_end)
            ))
        ).scalar_one() or 0
        # Newly scored on this day: servers whose first_score is within the day
        newly_cnt = sum(1 for ts in first_score_map.values() if day_start <= ts < day_end)
        series.append(BurndownEntry(date=day.isoformat(), never_scored_count=never_cnt, newly_scored=newly_cnt))

    return NeverScoredBurndownResponse(
        as_of=now.isoformat(),
        never_scored_count=never_scored_count,
        total_registry_count=total_servers,
        burndown_series=series,
    )

if __name__ == "__main__":  # CI-safe self-test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = SessionLocal()
    # Seed 5 servers
    for i, (sid, src) in enumerate([
        ("srv1", "npm"),
        ("srv2", "npm"),
        ("srv3", "github"),
        ("srv4", "github"),
        ("srv5", "github"),
    ], start=1):
        db.add(McpServerRegistry(server_id=sid, name=f"Srv {i}", registry_source=src))
    db.commit()

    # Score three servers on different dates
    mv = "v3.0_40974559"
    now = datetime.now(timezone.utc)
    dates = [now - timedelta(days=2), now - timedelta(days=1), now]
    for i, (sid, dt) in enumerate(zip(["srv1", "srv2", "srv3"], dates), start=1):
        db.add(McpLlmAxisScore(
            id=i,
            server_id=sid,
            axis_name="overall_risk",
            label="HIGH",
            model_version=mv,
            scored_at=dt,
        ))
    db.commit()
    db.close()

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/api/reporting/never-scored-burndown?days=3")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_registry_count"] == 5
    assert data["never_scored_count"] == 2  # srv4, srv5 never scored
    assert len(data["burndown_series"]) >= 2
    print("PASS")
