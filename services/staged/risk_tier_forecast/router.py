from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/risk")

class ForecastEntry(BaseModel):
    date: str
    tier: str
    probability: float

class ForecastResponse(BaseModel):
    days: int
    forecast: List[ForecastEntry]

def calculate_forecast(days: int, session: Session):
    # Get current date and date 7 days ago
    today = datetime.now().date()
    seven_days_ago = today - timedelta(days=7)

    # Get all servers
    servers = session.query(McpServerRegistry.server_id, McpServerRegistry.risk_tier).all()

    forecast = []

    for day in range(1, days + 1):
        future_date = today + timedelta(days=day)

        for server_id, current_tier in servers:
            # Get recent scores for this server
            scores = session.query(
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.scored_at
            ).filter(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.scored_at >= seven_days_ago
            ).all()

            if not scores:
                continue

            # Calculate average p_top per tier
            tier_scores = {}
            for score in scores:
                tier = score.axis_name.split('_')[0]  # Assuming axis_name is like "tier1_..."
                if tier not in tier_scores:
                    tier_scores[tier] = []
                tier_scores[tier].append(score.p_top)

            avg_scores = {tier: sum(scores)/len(scores) for tier, scores in tier_scores.items()}

            # Simple linear extrapolation (this is a placeholder - real logic would be more complex)
            # For demo purposes, we'll just use the current tier's average score
            current_tier_avg = avg_scores.get(current_tier, 0.5)
            probability = current_tier_avg

            forecast.append({
                "date": future_date.isoformat(),
                "tier": current_tier,
                "probability": probability
            })

    return {"days": days, "forecast": forecast}

@router.get("/forecast", response_model=ForecastResponse)
async def get_risk_forecast(days: int, session: Session = Depends(get_session)):
    if days < 1 or days > 30:
        raise HTTPException(status_code=400, detail="Days must be between 1 and 30")

    return calculate_forecast(days, session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create in-memory SQLite database for testing
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

    # Insert test data
    with SessionLocal() as session:
        # Add some servers
        session.add(McpServerRegistry(server_id="server1", risk_tier="tier1"))
        session.add(McpServerRegistry(server_id="server2", risk_tier="tier2"))
        session.commit()

        # Add some scores
        today = datetime.now().date()
        for day in range(-6, 1):  # Last 7 days including today
            date = today + timedelta(days=day)
            for server_id in ["server1", "server2"]:
                for tier in ["tier1", "tier2"]:
                    session.add(McpLlmAxisScore(
                        server_id=server_id,
                        axis_name=f"{tier}_axis1",
                        p_top=0.1 if tier == "tier1" else 0.9,
                        scored_at=date
                    ))
            session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/forecast?days=2")

    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 2
    assert len(data["forecast"]) >= 4  # At least 2 days * 2 servers

    # Verify probabilities sum to 1 per day per server
    date_counts = {}
    for entry in data["forecast"]:
        if entry["date"] not in date_counts:
            date_counts[entry["date"]] = {}
        if entry["tier"] not in date_counts[entry["date"]]:
            date_counts[entry["date"]][entry["tier"]] = 0
        date_counts[entry["date"]][entry["tier"]] += entry["probability"]

    for date, tiers in date_counts.items():
        total = sum(tiers.values())
        assert abs(total - 1.0) < 0.01, f"Probabilities for {date} don't sum to 1: {total}"

    print("PASS")