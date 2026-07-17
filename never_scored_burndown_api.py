from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScores

router = APIRouter()

class DailyBurndownData(BaseModel):
    date: str
    never_scored_count: int
    newly_scored: int

class NeverScoredBurndownResponse(BaseModel):
    as_of: str
    never_scored_count: int
    total_registry_count: int
    burndown_series: List[DailyBurndownData]

def get_never_scored_count(session: Session) -> int:
    """Count servers in registry with no scores."""
    subquery = session.query(McpLlmAxisScores.server_id).distinct().subquery()
    return session.query(McpServerRegistry).filter(
        ~McpServerRegistry.server_id.in_(subquery)
    ).count()

def get_total_registry_count(session: Session) -> int:
    """Count all servers in registry."""
    return session.query(McpServerRegistry).count()

def get_burndown_series(session: Session, days: int) -> List[DailyBurndownData]:
    """Generate daily burndown data for the last N days."""
    series = []
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days)

    for day in range(days, 0, -1):
        current_date = today - timedelta(days=day)
        next_date = current_date + timedelta(days=1)

        # Get servers scored on this day
        scored_today = session.query(McpLlmAxisScores.server_id).filter(
            and_(
                McpLlmAxisScores.created_at >= current_date,
                McpLlmAxisScores.created_at < next_date
            )
        ).distinct().all()

        # Get servers never scored up to this day
        scored_up_to_date = session.query(McpLlmAxisScores.server_id).filter(
            McpLlmAxisScores.created_at < next_date
        ).distinct().subquery()

        never_scored = session.query(McpServerRegistry).filter(
            ~McpServerRegistry.server_id.in_(scored_up_to_date)
        ).count()

        # Get servers never scored up to previous day
        prev_date = current_date - timedelta(days=1)
        scored_up_to_prev = session.query(McpLlmAxisScores.server_id).filter(
            McpLlmAxisScores.created_at < prev_date
        ).distinct().subquery()

        never_scored_prev = session.query(McpServerRegistry).filter(
            ~McpServerRegistry.server_id.in_(scored_up_to_prev)
        ).count()

        newly_scored = never_scored_prev - never_scored

        series.append(DailyBurndownData(
            date=current_date.isoformat(),
            never_scored_count=never_scored,
            newly_scored=newly_scored
        ))

    return series

@router.get("/reporting/never-scored-burndown", response_model=NeverScoredBurndownResponse)
async def never_scored_burndown(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session)
):
    """Report servers never scored with daily burndown trend."""
    as_of = datetime.utcnow().isoformat()
    never_scored_count = get_never_scored_count(session)
    total_registry_count = get_total_registry_count(session)
    burndown_series = get_burndown_series(session, days)

    return NeverScoredBurndownResponse(
        as_of=as_of,
        never_scored_count=never_scored_count,
        total_registry_count=total_registry_count,
        burndown_series=burndown_series
    )

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry, McpLlmAxisScores
    from app.main import app

    # Create test database
    Base.metadata.create_all(engine)

    # Add test data
    test_session = Session(engine)
    test_session.add_all([
        McpServerRegistry(server_id=f"server{i}", name=f"Test Server {i}")
        for i in range(1, 11)
    ])
    test_session.add_all([
        McpLlmAxisScores(
            server_id=f"server{i}",
            created_at=datetime.utcnow() - timedelta(days=i)
        )
        for i in range(1, 6)
    ])
    test_session.commit()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: test_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/reporting/never-scored-burndown?days=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["burndown_series"]) >= 2
    assert data["never_scored_count"] > 0
    print("PASS")