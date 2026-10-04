# deps: fastapi, pydantic, sqlalchemy
"""never_scored_backlog_burndown_api -- historical burndown of never-scored backlog.

GET /api/registry/never-scored-burndown
  Returns day-by-day burndown: each day shows how many servers in the registry
  have never been scored, enabling tracking of backlog clearance velocity.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models (McpServerRegistry,
  McpLlmAxisScore). Postgres-portable SQLAlchemy ORM queries; no DuckDB-only
  constructs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["never_scored_backlog_burndown_api"])


# --------------------------------------------------------------------------- #
# Request / response shapes
# --------------------------------------------------------------------------- #

class BurndownPoint(BaseModel):
    date: str            # ISO-8601 date string (YYYY-MM-DD)
    total_registry: int   # cumulative servers ever registered through this date
    never_scored: int    # servers registered through this date with NO axis scores
    scored: int          # servers registered through this date that have at least one score


class BurndownResponse(BaseModel):
    as_of: str
    days: int
    total_never_scored: int    # current never-scored count (end of series)
    total_scored: int          # cumulative scored (end of series)
    total_registry: int         # total registry size (end of series)
    series: List[BurndownPoint]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/registry/never-scored-burndown", response_model=BurndownResponse)
def never_scored_burndown(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> BurndownResponse:
    """
    Return a day-by-day burndown of never-scored servers over the last N days.

    Each row is cumulative: for every date d in the range, the row shows how many
    servers in the registry (ever, including before d) still had no axis scores
    as of that date.  The series is sorted oldest-first; the backlog appears as
    an ever-decreasing `never_scored` value when scoring keeps pace with intake.
    """
    now = datetime.now(timezone.utc)

    # Get the set of server_ids that have at least one axis score (ever scored)
    scored_ids: set[str] = set(
        row[0]
        for row in db.execute(
            select(distinct(McpLlmAxisScore.server_id))
        ).all()
    )

    # All registered servers with a first_seen, ordered oldest-first
    all_servers = (
        db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.first_seen)
            .where(McpServerRegistry.first_seen.isnot(None))
            .order_by(McpServerRegistry.first_seen)
        )
        .all()
    )

    if not all_servers:
        return BurndownResponse(
            as_of=now.isoformat(),
            days=days,
            total_never_scored=0,
            total_scored=0,
            total_registry=0,
            series=[],
        )

    # Build per-date cumulative counts.
    # A server counts toward `never_scored` on date d iff:
    #   - it first appeared on or before d  (first_seen <= d)
    #   - it has never been scored           (server_id not in scored_ids)
    daily: dict[str, dict] = {}   # keyed by "YYYY-MM-DD"
    cumulative_registry = 0
    cumulative_scored = 0
    cumulative_never_scored = 0

    # We need to know which servers were already scored BEFORE each date so we
    # can attribute the score event to the correct day.  For each scored server
    # use its earliest scored_at.
    scored_earliest: dict[str, datetime] = {}
    scored_rows = (
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                func.min(McpLlmAxisScore.scored_at)
            )
            .group_by(McpLlmAxisScore.server_id)
        )
        .all()
    )
    for server_id, min_scored_at in scored_rows:
        if min_scored_at:
            scored_earliest[server_id] = min_scored_at

    # Snapshot dates: every day in the range
    from datetime import timedelta
    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    dates_set = set(dates)

    # We'll snapshot after processing each day's events
    for date_str in dates:
        # Server arrivals on this date
        arrivals = [
            (sid, fs)
            for sid, fs in all_servers
            if fs and fs.date().isoformat() == date_str
        ]

        # Servers first scored on this date
        newly_scored = [
            sid for sid, sa in scored_earliest.items()
            if sa and sa.date().isoformat() == date_str
        ]

        cumulative_registry += len(arrivals)
        for sid, _ in arrivals:
            if sid not in scored_ids:
                cumulative_never_scored += 1
        cumulative_scored += len(newly_scored)
        # Never-scored decreases by the servers that just got their first score
        cumulative_never_scored -= len(newly_scored)

        if date_str in dates_set:
            daily[date_str] = {
                "date": date_str,
                "total_registry": cumulative_registry,
                "never_scored": cumulative_never_scored,
                "scored": cumulative_scored,
            }

    series = [BurndownPoint(**daily[d]) for d in sorted(daily) if d in daily]

    return BurndownResponse(
        as_of=now.isoformat(),
        days=days,
        total_never_scored=cumulative_never_scored,
        total_scored=cumulative_scored,
        total_registry=cumulative_registry,
        series=series,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    today = datetime.now(timezone.utc).date()

    with engine.connect() as conn:
        # Insert servers with first_seen dates spread across 5 days
        rows = [
            ("srv1", "Srv1", "npm",   "HIGH",   (today - timedelta(days=5)).isoformat()),
            ("srv2", "Srv2", "npm",   "HIGH",   (today - timedelta(days=5)).isoformat()),
            ("srv3", "Srv3", "npm",   "MEDIUM", (today - timedelta(days=4)).isoformat()),
            ("srv4", "Srv4", "github","LOW",    (today - timedelta(days=2)).isoformat()),
            ("srv5", "Srv5", "github","LOW",    (today - timedelta(days=2)).isoformat()),
        ]
        for sid, name, src, tier, fs in rows:
            conn.execute(text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, registry_source, risk_tier, url, first_seen, last_seen, scan_count, confidence) "
                "VALUES (:sid, :name, :src, :tier, :url, :fs, NULL, 0, 0.5)"
            ), {"sid": sid, "name": name, "src": src, "tier": tier,
                "url": f"https://example.com/{sid}", "fs": fs})
        conn.commit()

        # srv1 gets scored on day -5 (arrival day)
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, model_version, label, scored_at) "
            "VALUES ('srv1', 'overall_risk', 'v1', 'MEDIUM', :ts)"
        ), {"ts": f"{(today - timedelta(days=5)).isoformat()} 00:00:00"})
        # srv3 gets scored on day -4 (next day)
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, model_version, label, scored_at) "
            "VALUES ('srv3', 'overall_risk', 'v1', 'LOW', :ts)"
        ), {"ts": f"{(today - timedelta(days=4)).isoformat()} 00:00:00"})
        # srv5 gets scored on day -2 (same day as arrival)
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, model_version, label, scored_at) "
            "VALUES ('srv5', 'overall_risk', 'v1', 'LOW', :ts)"
        ), {"ts": f"{(today - timedelta(days=2)).isoformat()} 00:00:00"})
        conn.commit()

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
    resp = client.get("/api/registry/never-scored-burndown?days=5")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # 5 servers total; 3 scored (srv1, srv3, srv5); 2 never-scored (srv2, srv4)
    assert data["total_registry"] == 5, f"total_registry: expected 5, got {data['total_registry']}"
    assert data["total_scored"] == 3, f"total_scored: expected 3, got {data['total_scored']}"
    assert data["total_never_scored"] == 2, f"total_never_scored: expected 2, got {data['total_never_scored']}"
    assert len(data["series"]) == 5, f"expected 5 daily rows, got {len(data['series'])}"

    # Verify never_scored decreases as servers get scored
    never_scores = [pt["never_scored"] for pt in data["series"]]
    # Day -5: 2 arrived, 1 scored → 1 never-scored (start=1)
    # Day -4: 1 arrived, 1 scored → 1 never-scored (still 1)
    # Day -3: 0 arrived, 0 scored → 1 never-scored (still 1)
    # Day -2: 2 arrived, 1 scored → 2 never-scored (1→2)
    # Day -1: 0 arrived, 0 scored → 2 never-scored (still 2)
    assert never_scores == [1, 1, 1, 2, 2], f"never_scores timeline mismatch: {never_scores}"

    print("PASS")
    sys.exit(0)
