# deps: fastapi, pydantic, sqlalchemy
"""mcp_scoring_burndown -- scoring velocity burndown: servers scored per day vs registry growth.

GET /api/registry/scoring-burndown
  Returns day-by-day burndown showing how many new servers entered the registry,
  how many axis scores were emitted, and the cumulative scored vs never-scored
  backlog.  Velocity = new scores / new registry entries per day.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on McpServerRegistry,
  McpLlmAxisScore. Postgres-portable queries only; no DuckDB-only constructs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, distinct, case
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["mcp_scoring_burndown"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class BurndownPoint(BaseModel):
    date: str
    new_registry_entries: int
    new_scores: int
    cumulative_registry: int
    cumulative_scored: int
    never_scored: int
    velocity_pct: float  # new_scores / max(new_registry_entries, 1) * 100


class ScoringBurndownResponse(BaseModel):
    as_of: str
    days: int
    total_scored: int
    total_never_scored: int
    total_registry: int
    avg_velocity_pct: float
    series: List[BurndownPoint]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/registry/scoring-burndown",
    response_model=ScoringBurndownResponse,
    name="mcp_scoring_burndown:get",
)
def scoring_burndown(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ScoringBurndownResponse:
    """
    Day-by-day scoring velocity burndown.

    For each date in the last N days:
      - new_registry_entries : servers whose first_seen == this date
      - new_scores           : axis score rows whose scored_at falls on this date
      - cumulative_registry  : count of all servers with first_seen <= this date
      - cumulative_scored    : count of distinct servers with >=1 score with scored_at <= this date
      - never_scored         : cumulative_registry - cumulative_scored
      - velocity_pct         : new_scores / max(new_registry_entries, 1) * 100

    A healthy burndown has a rising cumulative_scored and shrinking never_scored.
    Velocity_pct > 100 means scoring is catching up faster than new arrivals.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    date_range = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    date_set = set(date_range)

    # ---- daily new registry arrivals ----
    arrivals_by_date: dict[str, int] = {d: 0 for d in date_set}
    arrivals_q = (
        db.execute(
            select(
                func.date(McpServerRegistry.first_seen).label("d"),
                func.count().label("cnt"),
            )
            .where(McpServerRegistry.first_seen.isnot(None))
            .where(func.date(McpServerRegistry.first_seen).in_(date_set))
            .group_by(func.date(McpServerRegistry.first_seen))
        )
        .all()
    )
    for row in arrivals_q:
        if row[0]:
            key = str(row[0])
            if key in arrivals_by_date:
                arrivals_by_date[key] = row[1]

    # ---- daily new scores ----
    scores_by_date: dict[str, int] = {d: 0 for d in date_set}
    scores_q = (
        db.execute(
            select(
                func.date(McpLlmAxisScore.scored_at).label("d"),
                func.count().label("cnt"),
            )
            .where(McpLlmAxisScore.scored_at.isnot(None))
            .where(func.date(McpLlmAxisScore.scored_at).in_(date_set))
            .group_by(func.date(McpLlmAxisScore.scored_at))
        )
        .all()
    )
    for row in scores_q:
        if row[0]:
            key = str(row[0])
            if key in scores_by_date:
                scores_by_date[key] = row[1]

    # ---- cumulative totals (end of period) ----
    total_registry = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    scored_ids = {
        r[0]
        for r in db.execute(
            select(distinct(McpLlmAxisScore.server_id))
        ).all()
    }
    total_scored = len(scored_ids)
    total_never_scored = total_registry - total_scored

    # ---- build series ----
    series: List[BurndownPoint] = []
    cumulative_registry = 0
    cumulative_scored = 0
    # track per-date cumulative scored using distinct server_ids seen through each date
    seen_scored_ids: set[str] = set()
    # snapshot of scored server_ids at each date (server scored_at <= date)
    scored_at_by_date: dict[str, set[str]] = {d: set() for d in date_set}

    scored_server_date_q = (
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                func.date(McpLlmAxisScore.scored_at).label("d"),
            )
            .where(McpLlmAxisScore.scored_at.isnot(None))
            .where(func.date(McpLlmAxisScore.scored_at).in_(date_set))
        )
        .all()
    )
    for sid, d in scored_server_date_q:
        if d and str(d) in scored_at_by_date:
            scored_at_by_date[str(d)].add(sid)

    for date_str in sorted(date_set):
        new_entries = arrivals_by_date.get(date_str, 0)
        new_scores = scores_by_date.get(date_str, 0)
        cumulative_registry += new_entries

        # Add servers whose first_seen is today and who are already scored
        cumulative_scored = len(scored_at_by_date.get(date_str, set()))

        # Never-scored = cumulative registered that have never appeared in any score table
        # through this date
        never_scored = cumulative_registry - cumulative_scored
        never_scored = max(never_scored, 0)

        velocity_pct = round((new_scores / max(new_entries, 1)) * 100, 2)

        series.append(
            BurndownPoint(
                date=date_str,
                new_registry_entries=new_entries,
                new_scores=new_scores,
                cumulative_registry=cumulative_registry,
                cumulative_scored=cumulative_scored,
                never_scored=never_scored,
                velocity_pct=velocity_pct,
            )
        )

    avg_velocity = (
        round(sum(p.velocity_pct for p in series) / len(series), 2)
        if series
        else 0.0
    )

    return ScoringBurndownResponse(
        as_of=now.isoformat(),
        days=days,
        total_scored=total_scored,
        total_never_scored=total_never_scored,
        total_registry=total_registry,
        avg_velocity_pct=avg_velocity,
        series=series,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
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

    # Seed via ORM so autoincrement ids are assigned correctly
    with SessionLocal() as sess:
        for i, offset in enumerate([4, 3, 2, 1, 0]):
            srv = McpServerRegistry(
                server_id=f"srv{i+1}",
                name=f"Server srv{i+1}",
                registry_source="github",
                risk_tier="MEDIUM",
                url=f"https://github.com/srv{i+1}",
                first_seen=datetime.fromisoformat((today - timedelta(days=offset)).isoformat()),
                scan_count=0,
                confidence=0.5,
            )
            sess.add(srv)

        # 2 scores on day -4 (srv1 scored)
        for axis, label in [("overall_risk", "MEDIUM"), ("auth_strength", "LOW")]:
            sess.add(McpLlmAxisScore(
                server_id="srv1",
                axis_name=axis,
                model_version="v1",
                label=label,
                scored_at=datetime.fromisoformat(f"{(today - timedelta(days=4)).isoformat()} 01:00:00"),
            ))
        # 1 score on day -3 (srv2 scored)
        sess.add(McpLlmAxisScore(
            server_id="srv2",
            axis_name="overall_risk",
            model_version="v1",
            label="HIGH",
            scored_at=datetime.fromisoformat(f"{(today - timedelta(days=3)).isoformat()} 01:00:00"),
        ))
        # 1 score on day -1 (srv4 scored)
        sess.add(McpLlmAxisScore(
            server_id="srv4",
            axis_name="overall_risk",
            model_version="v1",
            label="LOW",
            scored_at=datetime.fromisoformat(f"{(today - timedelta(days=1)).isoformat()} 01:00:00"),
        ))
        sess.commit()

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

    # Happy path
    resp = client.get("/api/registry/scoring-burndown?days=5")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert "series" in data and len(data["series"]) == 5, (
        f"Expected 5 series points, got {len(data.get('series', []))}"
    )
    assert data["total_registry"] == 5, f"Expected 5 total_registry, got {data['total_registry']}"
    assert data["total_scored"] == 3, f"Expected 3 total_scored, got {data['total_scored']}"
    assert data["total_never_scored"] == 2, f"Expected 2 total_never_scored, got {data['total_never_scored']}"
    assert "avg_velocity_pct" in data

    # Verify structure of each point
    for pt in data["series"]:
        for field in ("date", "new_registry_entries", "new_scores",
                      "cumulative_registry", "cumulative_scored",
                      "never_scored", "velocity_pct"):
            assert field in pt, f"Missing field {field} in point {pt}"

    # day -4: 1 arrival, 2 scores
    pt0 = data["series"][0]
    assert pt0["new_registry_entries"] == 1, f"day-4 arrivals: expected 1, got {pt0['new_registry_entries']}"
    assert pt0["new_scores"] == 2, f"day-4 scores: expected 2, got {pt0['new_scores']}"

    # day -3: 1 arrival, 1 score
    pt1 = data["series"][1]
    assert pt1["new_registry_entries"] == 1, f"day-3 arrivals: expected 1, got {pt1['new_registry_entries']}"
    assert pt1["new_scores"] == 1, f"day-3 scores: expected 1, got {pt1['new_scores']}"

    # day 0 (today): 1 arrival, 0 scores
    pt_last = data["series"][-1]
    assert pt_last["new_registry_entries"] == 1, f"today arrivals: expected 1, got {pt_last['new_registry_entries']}"
    assert pt_last["new_scores"] == 0, f"today scores: expected 0, got {pt_last['new_scores']}"

    print("PASS")
    sys.exit(0)
