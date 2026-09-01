from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
import statistics

app = FastAPI()

class ForecastEntry(BaseModel):
    date: str
    tier: str
    probability: float

class ForecastResponse(BaseModel):
    days: int
    forecast: List[ForecastEntry]

def get_recent_scores(db: Session, server_id: str, days: int = 7):
    cutoff = datetime.now() - timedelta(days=days)
    return db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= cutoff
    ).all()

def predict_tier_probabilities(recent_scores: List[McpLlmAxisScore], forecast_days: int):
    if not recent_scores:
        return []

    # Calculate average p_top per tier over recent days
    tier_averages = {}
    for score in recent_scores:
        if score.tier not in tier_averages:
            tier_averages[score.tier] = []
        tier_averages[score.tier].append(score.p_top)

    # Calculate mean p_top for each tier
    tier_means = {tier: statistics.mean(values) for tier, values in tier_averages.items()}

    # Normalize to probabilities (simple linear extrapolation)
    total = sum(tier_means.values())
    if total == 0:
        return []

    probabilities = {tier: mean / total for tier, mean in tier_means.items()}

    # Generate forecast entries
    forecast = []
    for day in range(1, forecast_days + 1):
        forecast_date = (datetime.now() + timedelta(days=day)).strftime('%Y-%m-%d')
        for tier, prob in probabilities.items():
            forecast.append(ForecastEntry(
                date=forecast_date,
                tier=tier,
                probability=prob
            ))

    return forecast

@app.get("/api/risk/forecast", response_model=ForecastResponse)
async def get_risk_forecast(days: int, db: Session = Depends(get_session)):
    if days <= 0 or days > 30:
        raise HTTPException(status_code=400, detail="days must be between 1 and 30")

    servers = db.query(McpServerRegistry).all()
    forecast = []

    for server in servers:
        recent_scores = get_recent_scores(db, server.server_id)
        server_forecast = predict_tier_probabilities(recent_scores, days)
        forecast.extend(server_forecast)

    return {"days": days, "forecast": forecast}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Insert test data
    test_session = TestSession()
    test_session.add_all([
        McpServerRegistry(server_id="server1", risk_tier="low"),
        McpServerRegistry(server_id="server2", risk_tier="medium"),
        McpLlmAxisScore(server_id="server1", axis_name="axis1", p_top=0.8, scored_at=datetime.now() - timedelta(days=1), tier="low"),
        McpLlmAxisScore(server_id="server1", axis_name="axis2", p_top=0.7, scored_at=datetime.now() - timedelta(days=2), tier="low"),
        McpLlmAxisScore(server_id="server1", axis_name="axis1", p_top=0.9, scored_at=datetime.now() - timedelta(days=3), tier="low"),
        McpLlmAxisScore(server_id="server2", axis_name="axis1", p_top=0.6, scored_at=datetime.now() - timedelta(days=1), tier="medium"),
        McpLlmAxisScore(server_id="server2", axis_name="axis2", p_top=0.5, scored_at=datetime.now() - timedelta(days=2), tier="medium"),
        McpLlmAxisScore(server_id="server2", axis_name="axis1", p_top=0.7, scored_at=datetime.now() - timedelta(days=3), tier="medium"),
    ])
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/forecast?days=2")

    assert response.status_code == 200
    data = response.json()
    assert "forecast" in data
    assert len(data["forecast"]) > 0

    # Verify probabilities sum to 1 per day
    dates = set()
    for entry in data["forecast"]:
        dates.add(entry["date"])

    for date in dates:
        daily_probs = [entry["probability"] for entry in data["forecast"] if entry["date"] == date]
        assert abs(sum(daily_probs) - 1.0) < 0.01, f"Probabilities for {date} do not sum to 1"

    print("PASS")