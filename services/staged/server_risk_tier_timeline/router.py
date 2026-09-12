from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class TimelineItem(BaseModel):
    date: str
    tier: str
    count: int


class TimelineResponse(BaseModel):
    server_id: str
    days: int
    timeline: List[TimelineItem]


@router.get(
    "/server/{server_id}/risk_tier_timeline",
    response_model=TimelineResponse,
    name="server_risk_tier_timeline",
)
def get_risk_tier_timeline(
    server_id: str,
    days: int = Query(30, ge=1),
    db: Session = Depends(get_session),
):
    """
    Return the historical risk tier progression for a given server.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    stmt = text(
        """
        SELECT
            DATE_TRUNC('day', s.scored_at) AS day,
            r.risk_tier AS tier,
            COUNT(*) AS cnt
        FROM McpLlmAxisScore AS s
        JOIN McpServerRegistry AS r
          ON s.server_id = r.server_id
        WHERE s.axis_name = :axis_name
          AND s.server_id = :server_id
          AND s.scored_at >= :cutoff
        GROUP BY day, tier
        ORDER BY day ASC
        """
    )

    rows = db.execute(
        stmt,
        {"axis_name": "overall_risk", "server_id": server_id, "cutoff": cutoff},
    ).fetchall()

    timeline = [
        TimelineItem(
            date=row.day.strftime("%Y-%m-%d"),
            tier=row.tier,
            count=row.cnt,
        )
        for row in rows
    ]

    return TimelineResponse(server_id=server_id, days=days, timeline=timeline)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import os

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Create an in‑memory SQLite DB and bind the app models to it
    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    # Import Base from the models module (assumed to be the declarative base)
    from app.models import Base  # type: ignore

    Base.metadata.create_all(bind=engine)

    # Populate sample data
    db: Session = SessionLocal()
    try:
        # Registry entry
        db.add(
            McpServerRegistry(
                server_id="srv1",
                risk_tier="high",
            )
        )
        # Scores over three consecutive days
        today = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        for i in range(3):
            db.add(
                McpLlmAxisScore(
                    server_id="srv1",
                    axis_name="overall_risk",
                    scored_at=today - timedelta(days=i),
                )
            )
        db.commit()
    finally:
        db.close()

    # Build FastAPI app and override the session dependency
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/server/srv1/risk_tier_timeline?days=3")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == "srv1"
    assert data["days"] == 3
    timeline = data["timeline"]
    assert len(timeline) == 3, f"Expected 3 timeline entries, got {len(timeline)}"

    # Verify count for the oldest date (today - 2 days)
    oldest = timeline[0]
    expected_date = (today - timedelta(days=2)).strftime("%Y-%m-%d")
    assert oldest["date"] == expected_date, f"Expected date {expected_date}"
    assert oldest["tier"] == "high"
    assert oldest["count"] == 1

    print("PASS")