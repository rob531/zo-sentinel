# services/staged/server_risk_tier_timeline/logic.py
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Base

router = APIRouter()


class TimelineEntry(BaseModel):
    date: str
    tier: str
    count: int


class TimelineResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[TimelineEntry]


def _fetch_timeline(session: Session, server_id: str, days: int) -> List[dict]:
    """Return timeline rows for a server."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    q = (
        session.query(
            func.date(McpLlmAxisScore.scored_at).label("date"),
            McpServerRegistry.risk_tier.label("tier"),
            func.count().label("count"),
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .group_by("date", "tier")
        .order_by("date")
    )
    rows = q.all()
    return [{"date": r.date, "tier": r.tier, "count": r.count} for r in rows]


@router.get(
    "/api/server/{server_id}/risk_tier_timeline",
    response_model=TimelineResponse,
)
def get_risk_tier_timeline(
    server_id: str,
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
):
    timeline = _fetch_timeline(session, server_id, days)
    return {"server_id": server_id, "days": days, "timeline": timeline}


# Compatibility alias for other services that expect this name
def get_risk_tier_history(session: Session, server_id: str, days: int) -> List[dict]:
    return _fetch_timeline(session, server_id, days)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and initialise tables
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Override the dependency to use the test session
    app = FastAPI()
    app.include_router(router)


    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()


    app.dependency_overrides[get_session] = get_test_session

    # Insert sample data
    with SessionLocal() as db:
        # server registry entry
        db.add(McpServerRegistry(server_id="srv1", risk_tier="high"))
        # three days of scores, one per day
        base_dt = datetime.utcnow().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        for i in range(3):
            day_dt = base_dt - timedelta(days=i)
            db.add(
                McpLlmAxisScore(
                    server_id="srv1",
                    axis_name="overall_risk",
                    scored_at=day_dt,
                    score=0.5,
                )
            )
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/server/srv1/risk_tier_timeline?days=3")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == "srv1"
    assert data["days"] == 3
    assert len(data["timeline"]) == 3

    # Verify count for the most recent day
    recent_date = base_dt.date().isoformat()
    entry = next(
        (e for e in data["timeline"] if e["date"] == recent_date and e["tier"] == "high"),
        None,
    )
    assert entry is not None, "Missing timeline entry for recent date"
    assert entry["count"] == 1, f"Expected count 1, got {entry['count']}"

    print("PASS")