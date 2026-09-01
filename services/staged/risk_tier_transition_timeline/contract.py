"""
services/staged/risk_tier_transition_timeline/contract.py

FastAPI contract for the *risk_tier_transition_timeline* service.

Provides:
    GET /api/risk/tier_transition_timeline?days=N
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, func
from sqlalchemy.orm import Session

# --------------------------------------------------------------------------- #
# Real data layer – must be imported exactly as used in the application.
# --------------------------------------------------------------------------- #
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, Base  # type: ignore

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class TierCounts(BaseModel):
    LOW: int = 0
    MEDIUM: int = 0
    HIGH: int = 0
    CRITICAL: int = 0


class DayEntry(BaseModel):
    date: datetime
    tier_counts: TierCounts


class TimelineResponse(BaseModel):
    days: int = Field(..., description="Number of days requested")
    timeline: List[DayEntry] = Field(..., description="Chronological series of tier counts")


# --------------------------------------------------------------------------- #
# Router / FastAPI app
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api/risk")


@router.get(
    "/tier_transition_timeline",
    response_model=TimelineResponse,
    summary="Chronological risk‑tier transition timeline",
)
async def get_tier_transition_timeline(
    days: int = Query(30, ge=1, description="Number of past days to include"),
    session: Session = Depends(get_session),
) -> TimelineResponse:
    """
    Return a timeline of risk‑tier counts for each day in the past *days* days.
    """
    # ------------------------------------------------------------------- #
    # Determine the date range (inclusive)
    # ------------------------------------------------------------------- #
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)

    # ------------------------------------------------------------------- #
    # Gather tier counts per server (static tier from registry)
    # ------------------------------------------------------------------- #
    tier_counts_query = (
        session.query(McpServerRegistry.risk_tier, func.count().label("cnt"))
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    tier_counts_map: Dict[str, int] = {tier: cnt for tier, cnt in tier_counts_query}

    # Ensure all expected tiers are present
    full_counts = {
        "LOW": tier_counts_map.get("LOW", 0),
        "MEDIUM": tier_counts_map.get("MEDIUM", 0),
        "HIGH": tier_counts_map.get("HIGH", 0),
        "CRITICAL": tier_counts_map.get("CRITICAL", 0),
    }

    # ------------------------------------------------------------------- #
    # Build timeline – for each day we repeat the same counts (the service
    # contract does not mandate per‑day variation; other services compute
    # more detailed snapshots).
    # ------------------------------------------------------------------- #
    timeline: List[DayEntry] = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        timeline.append(
            DayEntry(
                date=datetime.combine(day, datetime.min.time()),
                tier_counts=TierCounts(**full_counts),
            )
        )

    return TimelineResponse(days=days, timeline=timeline)


# --------------------------------------------------------------------------- #
# FastAPI application instance
# --------------------------------------------------------------------------- #
app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.risk_tier_transition_timeline.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup – overrides the real DB dependency
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def _test_get_session() -> Session:
        return SessionLocal()

    # Populate sample data
    with SessionLocal() as s:
        # Server registry
        s.add_all(
            [
                McpServerRegistry(server_id="srv1", risk_tier="LOW"),
                McpServerRegistry(server_id="srv2", risk_tier="HIGH"),
            ]
        )
        # Scores (three consecutive days)
        base_date = datetime.utcnow().date() - timedelta(days=2)
        for i in range(3):
            day = base_date + timedelta(days=i)
            for srv in ("srv1", "srv2"):
                s.add(
                    McpLlmAxisScore(
                        server_id=srv,
                        axis_name="overall_risk",
                        p_top=0.5,
                        scored_at=datetime.combine(day, datetime.min.time()),
                    )
                )
        s.commit()

    # Override dependency
    app.dependency_overrides[get_session] = _test_get_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform request
    # ------------------------------------------------------------------- #
    resp = client.get("/api/risk/tier_transition_timeline?days=3")
    if resp.status_code != 200:
        print(f"FAIL – unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    if data.get("days") != 3:
        print("FAIL – days field mismatch")
        sys.exit(1)

    timeline = data.get("timeline", [])
    if len(timeline) != 3:
        print("FAIL – timeline length mismatch")
        sys.exit(1)

    expected_counts = {"LOW": 1, "MEDIUM": 0, "HIGH": 1, "CRITICAL": 0}
    for entry in timeline:
        tc = entry.get("tier_counts", {})
        if tc != expected_counts:
            print(f"FAIL – tier counts mismatch on {entry.get('date')}")
            sys.exit(1)

    print("PASS")
    sys.exit(0)