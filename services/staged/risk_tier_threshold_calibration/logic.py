from typing import Dict, List
from datetime import datetime, timedelta
from fastapi import Depends
from sqlalchemy import func, and_
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

def compute_calibration(days: int) -> Dict:
    session = Depends(get_session)

    # Calculate the date range for the query
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Query to get all scores within the date range
    scores = session.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.p_danger,
        McpLlmAxisScore.scored_at
    ).filter(
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).all()

    # Query to get current risk tiers for all servers
    servers = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier
    ).all()

    # Create a mapping of server_id to risk_tier
    server_to_tier = {server.server_id: server.risk_tier for server in servers}

    # Initialize the calibration dictionary
    calibration = {
        "LOW": {"p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "MEDIUM": {"p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0},
        "HIGH": {"p_top": 0.0, "p_critical": 0.0, "p_danger": 0.0}
    }

    # Count the number of scores for each risk tier
    tier_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for server_id, risk_tier in server_to_tier.items():
        if risk_tier in tier_counts:
            tier_counts[risk_tier] += 1

    # Calculate the percentile thresholds for each risk tier
    for server_id, axis_name, p_top, p_critical, p_danger, scored_at in scores:
        risk_tier = server_to_tier.get(server_id)
        if risk_tier:
            calibration[risk_tier]["p_top"] += p_top
            calibration[risk_tier]["p_critical"] += p_critical
            calibration[risk_tier]["p_danger"] += p_danger

    # Calculate the average for each risk tier
    for tier in calibration:
        if tier_counts[tier] > 0:
            calibration[tier]["p_top"] /= tier_counts[tier]
            calibration[tier]["p_critical"] /= tier_counts[tier]
            calibration[tier]["p_danger"] /= tier_counts[tier]

    return {"days": days, "calibration": calibration}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create a test app and client
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: session

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()

    # Insert sample data
    from datetime import datetime, timedelta
    yesterday = datetime.utcnow() - timedelta(days=1)
    today = datetime.utcnow()

    # Insert sample servers
    session.add_all([
        McpServerRegistry(server_id="server1", risk_tier="LOW"),
        McpServerRegistry(server_id="server2", risk_tier="MEDIUM"),
        McpServerRegistry(server_id="server3", risk_tier="HIGH"),
    ])

    # Insert sample scores
    session.add_all([
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis1",
            p_top=0.1,
            p_critical=0.2,
            p_danger=0.3,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="axis2",
            p_top=0.2,
            p_critical=0.3,
            p_danger=0.4,
            scored_at=today
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis1",
            p_top=0.3,
            p_critical=0.4,
            p_danger=0.5,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="axis2",
            p_top=0.4,
            p_critical=0.5,
            p_danger=0.6,
            scored_at=today
        ),
        McpLlmAxisScore(
            server_id="server3",
            axis_name="axis1",
            p_top=0.5,
            p_critical=0.6,
            p_danger=0.7,
            scored_at=yesterday
        ),
        McpLlmAxisScore(
            server_id="server3",
            axis_name="axis2",
            p_top=0.6,
            p_critical=0.7,
            p_danger=0.8,
            scored_at=today
        ),
    ])

    session.commit()

    # Test the compute_calibration function
    result = compute_calibration(2)
    expected = {
        "days": 2,
        "calibration": {
            "LOW": {"p_top": 0.15, "p_critical": 0.25, "p_danger": 0.35},
            "MEDIUM": {"p_top": 0.35, "p_critical": 0.45, "p_danger": 0.55},
            "HIGH": {"p_top": 0.55, "p_critical": 0.65, "p_danger": 0.75}
        }
    }

    assert result == expected, f"Expected {expected}, got {result}"
    print("PASS")