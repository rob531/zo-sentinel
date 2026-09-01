# services/staged/risk_tier_transition_timeline/logic.py
from datetime import datetime, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


def _score_to_tier(p_top: float) -> str:
    """Map a numeric risk score to a tier name."""
    if p_top < 25:
        return "LOW"
    if p_top < 50:
        return "MEDIUM"
    if p_top < 75:
        return "HIGH"
    return "CRITICAL"


class TierCounts(BaseModel):
    LOW: int = 0
    MEDIUM: int = 0
    HIGH: int = 0
    CRITICAL: int = 0


class DayEntry(BaseModel):
    date: str
    tier_counts: TierCounts


class TimelineResponse(BaseModel):
    days: int
    timeline: List[DayEntry]


@router.get(
    "/api/risk/tier_transition_timeline",
    response_model=TimelineResponse,
    tags=["risk"],
)
async def get_risk_tier_transition_timeline(
    days: int = 30, db: Session = Depends(get_session)
):
    if days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive")
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days - 1)

    # fetch scores for the requested window
    score_rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at >= start_date,
        )
        .all()
    )

    # organize scores per server, sorted by timestamp
    server_scores: Dict[int, List[tuple[datetime, float]]] = {}
    for row in score_rows:
        server_scores.setdefault(row.server_id, []).append((row.scored_at, row.p_top))
    for lst in server_scores.values():
        lst.sort(key=lambda x: x[0])

    # fallback registry tier (static tier) for servers without scores
    registry_map = {
        reg.server_id: reg.risk_tier for reg in db.query(McpServerRegistry).all()
    }

    timeline: List[Dict] = []
    all_server_ids = set(server_scores.keys()) | set(registry_map.keys())

    for offset in range(days):
        cur_date = start_date + timedelta(days=offset)
        tier_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
        for srv_id in all_server_ids:
            # latest score up to cur_date
            latest_score = None
            if srv_id in server_scores:
                for ts, val in reversed(server_scores[srv_id]):
                    if ts.date() <= cur_date:
                        latest_score = val
                        break
            if latest_score is not None:
                tier = _score_to_tier(latest_score)
            else:
                tier = registry_map.get(srv_id, "LOW")
            tier_counts[tier] += 1
        timeline.append(
            {"date": cur_date.isoformat(), "tier_counts": tier_counts}
        )

    return {"days": days, "timeline": timeline}


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the test
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Seed sample data
    db = SessionLocal()
    server1 = McpServerRegistry(server_id=1, risk_tier="LOW")
    server2 = McpServerRegistry(server_id=2, risk_tier="HIGH")
    db.add_all([server1, server2])

    today = datetime.utcnow().date()
    dates = [
        today - timedelta(days=2),
        today - timedelta(days=1),
        today,
    ]

    scores = [
        McpLlmAxisScore(
            server_id=1,
            axis_name="overall_risk",
            p_top=20,
            scored_at=datetime.combine(dates[0], datetime.min.time()),
        ),
        McpLlmAxisScore(
            server_id=1,
            axis_name="overall_risk",
            p_top=60,
            scored_at=datetime.combine(dates[1], datetime.min.time()),
        ),
        McpLlmAxisScore(
            server_id=2,
            axis_name="overall_risk",
            p_top=80,
            scored_at=datetime.combine(dates[2], datetime.min.time()),
        ),
    ]
    db.add_all(scores)
    db.commit()

    # Dependency override for the test session
    def get_test_session():
        try:
            yield SessionLocal()
        finally:
            pass

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/risk/tier_transition_timeline?days=3")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 3
    assert len(data["timeline"]) == 3

    expected = [
        {
            "date": dates[0].isoformat(),
            "tier_counts": {"LOW": 1, "MEDIUM": 0, "HIGH": 1, "CRITICAL": 0},
        },
        {
            "date": dates[1].isoformat(),
            "tier_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 2, "CRITICAL": 0},
        },
        {
            "date": dates[2].isoformat(),
            "tier_counts": {"LOW": 0, "MEDIUM": 0, "HIGH": 1, "CRITICAL": 1},
        },
    ]

    for exp, act in zip(expected, data["timeline"]):
        assert exp["date"] == act["date"]
        assert exp["tier_counts"] == act["tier_counts"]

    print("PASS")