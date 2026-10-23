"""
services/staged/server_timeline/contract.py

FastAPI contract for the `server_timeline` service.
Provides a chronological timeline of risk‑tier changes, axis score deltas,
and notable events for a given server.
"""

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, insert, select
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Real data‑layer imports (must remain unchanged for production)
# ----------------------------------------------------------------------
from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpAttestations,
    McpScoreDispute,
    Base,
)

# ----------------------------------------------------------------------
# Pydantic response models
# ----------------------------------------------------------------------
class TierTransition(BaseModel):
    date: datetime = Field(..., description="Timestamp of the transition")
    old_tier: str = Field(..., description="Previous risk tier")
    new_tier: str = Field(..., description="New risk tier")


class AxisDelta(BaseModel):
    date: datetime = Field(..., description="Timestamp of the delta (latest score)")
    axis_name: str = Field(..., description="Name of the axis")
    delta: float = Field(..., description="Change in p_top between latest and previous score")


class Events(BaseModel):
    scans: int = Field(..., description="Number of scan events")
    attestations: int = Field(..., description="Number of attestation events")
    disputes: int = Field(..., description="Number of dispute events")


class ServerTimelineResponse(BaseModel):
    server_id: str = Field(..., description="Target server identifier")
    days: int = Field(..., description="Number of days back the timeline covers")
    fetched_at: datetime = Field(..., description="When the data was fetched")
    tier_transitions: List[TierTransition] = Field(default_factory=list)
    axis_deltas: List[AxisDelta] = Field(default_factory=list)
    events: Events = Field(...)


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/timeline",
    response_model=ServerTimelineResponse,
    summary="Chronological timeline for a server",
)
def get_server_timeline(
    server_id: str,
    days: int = Query(30, ge=1, description="Number of days to look back"),
    session: Session = Depends(get_session),
) -> ServerTimelineResponse:
    """Fetch tier transitions, axis deltas and event counts for a server."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    # ------------------------------------------------------------------
    # Tier transitions
    # ------------------------------------------------------------------
    tier_col = next(
        (c for c in McpServerRegistry.__table__.columns if "tier" in c.name.lower()),
        None,
    )
    time_col = next(
        (c for c in McpServerRegistry.__table__.columns if isinstance(c.type, DateTime)),
        None,
    )
    if tier_col is None or time_col is None:
        raise HTTPException(status_code=500, detail="Server registry schema mismatch")

    stmt = (
        select(McpServerRegistry)
        .where(McpServerRegistry.server_id == server_id)
        .where(getattr(McpServerRegistry, time_col.name) >= cutoff)
        .order_by(getattr(McpServerRegistry, time_col.name))
    )
    registry_rows = session.execute(stmt).scalars().all()

    tier_transitions: List[TierTransition] = []
    prev_tier = None
    for row in registry_rows:
        cur_tier = getattr(row, tier_col.name)
        cur_time = getattr(row, time_col.name)
        if prev_tier is not None and cur_tier != prev_tier:
            tier_transitions.append(
                TierTransition(date=cur_time, old_tier=prev_tier, new_tier=cur_tier)
            )
        prev_tier = cur_tier

    # ------------------------------------------------------------------
    # Axis deltas
    # ------------------------------------------------------------------
    axis_time_col = next(
        (c for c in McpLlmAxisScore.__table__.columns if isinstance(c.type, DateTime)),
        None,
    )
    if axis_time_col is None:
        raise HTTPException(status_code=500, detail="Axis scores schema mismatch")

    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(getattr(McpLlmAxisScore, axis_time_col.name) >= cutoff)
        .order_by(getattr(McpLlmAxisScore, axis_time_col.name))
    )
    axis_rows = session.execute(stmt).scalars().all()

    axis_groups = {}
    for row in axis_rows:
        axis = row.axis_name
        axis_groups.setdefault(axis, []).append(row)

    axis_deltas: List[AxisDelta] = []
    for axis, rows in axis_groups.items():
        if len(rows) < 2:
            continue
        first, last = rows[0], rows[-1]
        delta = float(last.p_top) - float(first.p_top)
        axis_deltas.append(
            AxisDelta(date=getattr(last, axis_time_col.name), axis_name=axis, delta=delta)
        )

    # ------------------------------------------------------------------
    # Event counts
    # ------------------------------------------------------------------
    # Scans – using attestations as a proxy for scan events
    scans_stmt = (
        select(McpAttestations)
        .where(McpAttestations.server_id == server_id)
        .where(McpAttestations.created_at >= cutoff)
    )
    scans_cnt = session.execute(scans_stmt).scalar_one_or_none()
    scans_cnt = session.execute(scans_stmt).rowcount if scans_cnt is not None else 0

    attestations_stmt = (
        select(McpAttestations)
        .where(McpAttestations.server_id == server_id)
        .where(McpAttestations.created_at >= cutoff)
    )
    attestations_cnt = session.execute(attestations_stmt).rowcount

    disputes_stmt = (
        select(McpScoreDispute)
        .where(McpScoreDispute.server_id == server_id)
        .where(McpScoreDispute.created_at >= cutoff)
    )
    disputes_cnt = session.execute(disputes_stmt).rowcount

    events = Events(scans=scans_cnt, attestations=attestations_cnt, disputes=disputes_cnt)

    return ServerTimelineResponse(
        server_id=server_id,
        days=days,
        fetched_at=now,
        tier_transitions=tier_transitions,
        axis_deltas=axis_deltas,
        events=events,
    )


# ----------------------------------------------------------------------
# Self‑test (run with `python -m services.staged.server_timeline.contract`)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------
    # Build a throwaway SQLite DB and override the session dependency
    # ------------------------------------------------------------------
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    def get_test_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------
    # Helper to discover column names for each model
    # ------------------------------------------------------------------
    def _datetime_column(model):
        for col in model.__table__.columns:
            if isinstance(col.type, DateTime):
                return col.name
        raise RuntimeError(f"No DateTime column found for {model.__name__}")

    def _tier_column(model):
        for col in model.__table__.columns:
            if "tier" in col.name.lower():
                return col.name
        raise RuntimeError(f"No tier column found for {model.__name__}")

    # ------------------------------------------------------------------
    # Seed data
    # ------------------------------------------------------------------
    sess = SessionLocal()
    now = datetime.utcnow()

    # Server registry entries (server 1 will transition from low → medium)
    server_id = "srv-1"
    tier_col = _tier_column(McpServerRegistry)
    time_col = _datetime_column(McpServerRegistry)

    sess.execute(
        insert(McpServerRegistry).values(
            server_id=server_id,
            **{tier_col: "low", time_col: now - timedelta(days=4)},
        )
    )
    sess.execute(
        insert(McpServerRegistry).values(
            server_id=server_id,
            **{tier_col: "medium", time_col: now - timedelta(days=2)},
        )
    )
    # Additional server (no transition) – just to ensure multi‑server handling
    sess.execute(
        insert(McpServerRegistry).values(
            server_id="srv-2",
            **{tier_col: "low", time_col: now - timedelta(days=3)},
        )
    )

    # Axis scores for srv-1
    axis_time_col = _datetime_column(McpLlmAxisScore)
    sess.execute(
        insert(McpLlmAxisScore).values(
            server_id=server_id,
            axis_name="confidentiality",
            p_top=0.2,
            **{axis_time_col: now - timedelta(days=4)},
        )
    )
    sess.execute(
        insert(McpLlmAxisScore).values(
            server_id=server_id,
            axis_name="confidentiality",
            p_top=0.5,
            **{axis_time_col: now - timedelta(days=2)},
        )
    )

    # Attestations and disputes for srv-1
    sess.execute(
        insert(McpAttestations).values(
            server_id=server_id,
            created_at=now - timedelta(days=3),
        )
    )
    sess.execute(
        insert(McpScoreDispute).values(
            server_id=server_id,
            created_at=now - timedelta(days=1),
        )
    )
    sess.commit()

    # ------------------------------------------------------------------
    # Run test client
    # ------------------------------------------------------------------
    client = TestClient(app)

    resp = client.get(f"/api/servers/{server_id}/timeline?days=5")
    if resp.status_code != 200:
        print(f"FAIL – unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if not data.get("tier_transitions"):
        print("FAIL – no tier transitions returned", file=sys.stderr)
        sys.exit(1)

    # Verify known transition exists
    found = any(
        tt["old_tier"] == "low" and tt["new_tier"] == "medium"
        for tt in data["tier_transitions"]
    )
    if not found:
        print("FAIL – expected tier transition low→medium missing", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)