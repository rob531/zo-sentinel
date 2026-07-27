from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from pydantic import BaseModel
import requests

router = APIRouter(prefix="/api/risk")

class CalibrationResult(BaseModel):
    days: int
    calibration: Dict[str, Dict[str, float]]

def compute_calibration(days: int) -> Dict[str, Dict[str, float]]:
    session: Session = Depends(get_session)()
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        # Get all servers with their risk tiers
        servers = session.query(McpServerRegistry.server_id, McpServerRegistry.risk_tier).all()
        server_tiers = {server_id: risk_tier for server_id, risk_tier in servers}

        # Get all scores in the last N days
        scores = session.query(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.p_danger,
            McpLlmAxisScore.scored_at
        ).filter(
            McpLlmAxisScore.scored_at >= cutoff_date
        ).all()

        # Group scores by risk tier
        tier_scores = {}
        for server_id, risk_tier in server_tiers.items():
            if risk_tier not in tier_scores:
                tier_scores[risk_tier] = []
            for score in scores:
                if score.server_id == server_id:
                    tier_scores[risk_tier].append(score)

        # Calculate percentiles for each tier
        calibration = {}
        for tier, scores in tier_scores.items():
            if not scores:
                continue

            p_tops = [score.p_top for score in scores]
            p_criticals = [score.p_critical for score in scores]
            p_dangers = [score.p_danger for score in scores]

            calibration[tier] = {
                "p_top": sorted(p_tops)[len(p_tops) // 2] if p_tops else 0.0,
                "p_critical": sorted(p_criticals)[len(p_criticals) // 2] if p_criticals else 0.0,
                "p_danger": sorted(p_dangers)[len(p_dangers) // 2] if p_dangers else 0.0
            }

        return {"days": days, "calibration": calibration}
    finally:
        session.close()

@router.get("/tier_calibration", response_model=CalibrationResult)
async def get_tier_calibration(days: int = 30):
    return compute_calibration(days)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    # Create tables
    Base.metadata.create_all(bind=test_engine)

    # Mock data
    def get_test_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    # Override dependency
    app.dependency_overrides[get_session] = get_test_session

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Insert test data
    with TestSessionLocal() as session:
        # Insert servers
        session.add_all([
            McpServerRegistry(server_id="server1", risk_tier="LOW"),
            McpServerRegistry(server_id="server2", risk_tier="MEDIUM"),
            McpServerRegistry(server_id="server3", risk_tier="HIGH")
        ])

        # Insert scores for two days
        two_days_ago = datetime.utcnow() - timedelta(days=2)
        yesterday = datetime.utcnow() - timedelta(days=1)

        session.add_all([
            McpLlmAxisScore(
                server_id="server1",
                axis_name="axis1",
                p_top=0.1,
                p_critical=0.2,
                p_danger=0.3,
                scored_at=two_days_ago
            ),
            McpLlmAxisScore(
                server_id="server1",
                axis_name="axis2",
                p_top=0.2,
                p_critical=0.3,
                p_danger=0.4,
                scored_at=yesterday
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis_name="axis1",
                p_top=0.3,
                p_critical=0.4,
                p_danger=0.5,
                scored_at=two_days_ago
            ),
            McpLlmAxisScore(
                server_id="server2",
                axis_name="axis2",
                p_top=0.4,
                p_critical=0.5,
                p_danger=0.6,
                scored_at=yesterday
            ),
            McpLlmAxisScore(
                server_id="server3",
                axis_name="axis1",
                p_top=0.5,
                p_critical=0.6,
                p_danger=0.7,
                scored_at=two_days_ago
            ),
            McpLlmAxisScore(
                server_id="server3",
                axis_name="axis2",
                p_top=0.6,
                p_critical=0.7,
                p_danger=0.8,
                scored_at=yesterday
            )
        ])
        session.commit()

    # Test
    client = TestClient(test_app)
    response = client.get("/api/risk/tier_calibration?days=2")
    result = response.json()

    # Expected values (median of two days)
    expected = {
        "days": 2,
        "calibration": {
            "LOW": {
                "p_top": 0.15,
                "p_critical": 0.25,
                "p_danger": 0.35
            },
            "MEDIUM": {
                "p_top": 0.35,
                "p_critical": 0.45,
                "p_danger": 0.55
            },
            "HIGH": {
                "p_top": 0.55,
                "p_critical": 0.65,
                "p_danger": 0.75
            }
        }
    }

    assert result == expected
    print("PASS")