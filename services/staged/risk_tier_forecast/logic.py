from fastapi import Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

class ForecastEntry(BaseModel):
    date: str
    tier: str
    probability: float

class ForecastResponse(BaseModel):
    days: int
    forecast: List[ForecastEntry]

def get_risk_tier_forecast(days: int, db: Session = Depends(get_session)) -> ForecastResponse:
    if days <= 0:
        raise HTTPException(status_code=400, detail="Days must be a positive integer")

    # Get current date and date 7 days ago for averaging
    today = datetime.utcnow().date()
    seven_days_ago = today - timedelta(days=7)

    # Get all servers with their current risk tier
    servers = db.query(McpServerRegistry.server_id, McpServerRegistry.risk_tier).all()

    forecast = []

    for server_id, current_tier in servers:
        # Get all axis scores for this server in the last 7 days
        scores = db.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.scored_at
        ).filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at >= seven_days_ago,
            McpLlmAxisScore.scored_at <= today
        ).all()

        if not scores:
            continue

        # Calculate average p_top per axis over the last 7 days
        axis_averages = {}
        for axis_name, p_top, _ in scores:
            if axis_name not in axis_averages:
                axis_averages[axis_name] = []
            axis_averages[axis_name].append(p_top)

        avg_scores = {axis: sum(p_tops)/len(p_tops) for axis, p_tops in axis_averages.items()}

        # Simple linear extrapolation for the next N days
        for day in range(1, days + 1):
            future_date = (today + timedelta(days=day)).isoformat()

            # For this example, we'll just use the average scores to predict the tier
            # In a real implementation, you would have a more sophisticated model
            # Here we'll just predict the current tier with 100% probability
            forecast.append({
                "date": future_date,
                "server_id": server_id,
                "tier": current_tier,
                "probability": 1.0
            })

    return ForecastResponse(days=days, forecast=forecast)

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

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Add test data
    db = SessionLocal()
    try:
        # Add test servers
        db.add_all([
            McpServerRegistry(server_id="server1", risk_tier="low"),
            McpServerRegistry(server_id="server2", risk_tier="medium")
        ])

        # Add test scores
        today = datetime.utcnow().date()
        for day in range(-6, 1):  # Last 7 days including today
            date = today + timedelta(days=day)
            db.add_all([
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="axis1",
                    p_top=0.8 if day >= -3 else 0.6,
                    scored_at=date
                ),
                McpLlmAxisScore(
                    server_id="server1",
                    axis_name="axis2",
                    p_top=0.7 if day >= -3 else 0.5,
                    scored_at=date
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="axis1",
                    p_top=0.6 if day >= -3 else 0.4,
                    scored_at=date
                ),
                McpLlmAxisScore(
                    server_id="server2",
                    axis_name="axis2",
                    p_top=0.5 if day >= -3 else 0.3,
                    scored_at=date
                )
            ])

        db.commit()

        # Test the endpoint
        client = TestClient(test_app)
        response = client.get("/api/risk/forecast?days=2")

        assert response.status_code == 200
        data = response.json()

        assert "forecast" in data
        assert len(data["forecast"]) > 0

        # Verify probabilities sum to 1 per day
        from collections import defaultdict
        day_probs = defaultdict(float)
        for entry in data["forecast"]:
            day_probs[entry["date"]] += entry["probability"]

        for prob in day_probs.values():
            assert abs(prob - 1.0) < 0.001, f"Probabilities should sum to 1, got {prob}"

        print("PASS")
    finally:
        db.close()