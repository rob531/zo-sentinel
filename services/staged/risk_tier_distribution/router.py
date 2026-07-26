from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/risk")

def get_risk_tier_distribution(days: int, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Aggregate server counts by risk tier for each day in the past N days."""
    query = (
        session.query(
            McpLlmAxisScore.scored_at,
            McpServerRegistry.risk_tier,
        )
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .filter(McpLlmAxisScore.scored_at >= datetime.now() - timedelta(days=days))
        .group_by(
            McpLlmAxisScore.scored_at,
            McpServerRegistry.risk_tier,
        )
        .order_by(McpLlmAxisScore.scored_at)
    )

    results = query.all()

    distribution = []
    for scored_at, risk_tier in results:
        day = scored_at.date()
        found = False
        for entry in distribution:
            if entry["date"] == day.isoformat():
                entry["tier_counts"][risk_tier] = entry["tier_counts"].get(risk_tier, 0) + 1
                found = True
                break
        if not found:
            distribution.append({
                "date": day.isoformat(),
                "tier_counts": {risk_tier: 1}
            })

    return distribution

@router.get("/tier_distribution", response_model=Dict[str, Any])
async def tier_distribution(
    days: int = Query(..., description="Number of days to look back"),
    session: Session = Depends(get_session)
):
    """Endpoint to get risk tier distribution over the past N days."""
    distribution = get_risk_tier_distribution(days, session)
    return {"days": days, "distribution": distribution}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with SessionLocal() as session:
        # Add sample data for two days
        yesterday = datetime.now() - timedelta(days=1)
        day_before = datetime.now() - timedelta(days=2)

        # Day before yesterday
        session.add(McpServerRegistry(
            server_id="server1",
            risk_tier="TRUSTED_GENERAL",
            last_seen=day_before
        ))
        session.add(McpServerRegistry(
            server_id="server2",
            risk_tier="TRUSTED_RESEARCH",
            last_seen=day_before
        ))
        session.add(McpLlmAxisScore(
            server_id="server1",
            axis_name="axis1",
            p_top=0.9,
            scored_at=day_before
        ))
        session.add(McpLlmAxisScore(
            server_id="server2",
            axis_name="axis1",
            p_top=0.8,
            scored_at=day_before
        ))

        # Yesterday
        session.add(McpServerRegistry(
            server_id="server3",
            risk_tier="TRUSTED_GENERAL",
            last_seen=yesterday
        ))
        session.add(McpServerRegistry(
            server_id="server4",
            risk_tier="UNTRUSTED",
            last_seen=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server3",
            axis_name="axis1",
            p_top=0.7,
            scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server4",
            axis_name="axis1",
            p_top=0.6,
            scored_at=yesterday
        ))
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier_distribution?days=2")
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    assert len(data["distribution"]) == 2

    # Verify counts for each day
    for entry in data["distribution"]:
        if entry["date"] == (datetime.now() - timedelta(days=1)).date().isoformat():
            assert entry["tier_counts"]["TRUSTED_GENERAL"] == 1
            assert entry["tier_counts"]["UNTRUSTED"] == 1
        elif entry["date"] == (datetime.now() - timedelta(days=2)).date().isoformat():
            assert entry["tier_counts"]["TRUSTED_GENERAL"] == 1
            assert entry["tier_counts"]["TRUSTED_RESEARCH"] == 1

    print("PASS")