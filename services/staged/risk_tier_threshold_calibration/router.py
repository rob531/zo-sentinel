from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/risk")

def compute_calibration(days: int, session: Session) -> Dict[str, Any]:
    """Compute risk tier thresholds based on recent LLM axis scores."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Get all scores in the last N days
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.scored_at >= cutoff
    ).all()

    if not scores:
        raise HTTPException(status_code=404, detail="No scores found in the given period")

    # Group scores by server_id to find their risk_tier
    server_ids = {score.server_id for score in scores}
    server_tiers = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier
    ).filter(
        McpServerRegistry.server_id.in_(server_ids)
    ).all()

    tier_map = {tier.server_id: tier.risk_tier for tier in server_tiers}

    # Aggregate scores by risk tier
    tier_scores = {
        "LOW": {"p_top": [], "p_critical": [], "p_danger": []},
        "MEDIUM": {"p_top": [], "p_critical": [], "p_danger": []},
        "HIGH": {"p_top": [], "p_critical": [], "p_danger": []}
    }

    for score in scores:
        tier = tier_map.get(score.server_id)
        if tier:
            tier_scores[tier]["p_top"].append(score.p_top)
            tier_scores[tier]["p_critical"].append(score.p_critical)
            tier_scores[tier]["p_danger"].append(score.p_danger)

    # Compute percentiles for each tier
    calibration = {}
    for tier, metrics in tier_scores.items():
        if any(metrics.values()):
            calibration[tier] = {
                "p_top": _compute_percentile(metrics["p_top"], 0.9),
                "p_critical": _compute_percentile(metrics["p_critical"], 0.75),
                "p_danger": _compute_percentile(metrics["p_danger"], 0.5)
            }

    return {"days": days, "calibration": calibration}

def _compute_percentile(values: list, percentile: float) -> float:
    """Helper to compute a percentile from a list of values."""
    if not values:
        return 0.0
    values.sort()
    index = int(percentile * len(values))
    return values[index]

@router.get("/tier_calibration")
async def get_tier_calibration(days: int = 30, session: Session = Depends(get_session)):
    """Endpoint to get calibrated risk tier thresholds."""
    return compute_calibration(days, session)

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

    # Override the dependency for testing
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
        # Insert sample server registry data
        session.add(McpServerRegistry(server_id="server1", risk_tier="LOW"))
        session.add(McpServerRegistry(server_id="server2", risk_tier="MEDIUM"))
        session.add(McpServerRegistry(server_id="server3", risk_tier="HIGH"))

        # Insert sample LLM axis scores for two days
        yesterday = datetime.utcnow() - timedelta(days=1)
        today = datetime.utcnow()

        session.add(McpLlmAxisScore(
            server_id="server1", axis_name="trust",
            p_top=0.8, p_critical=0.6, p_danger=0.4, scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server2", axis_name="trust",
            p_top=0.9, p_critical=0.7, p_danger=0.5, scored_at=yesterday
        ))
        session.add(McpLlmAxisScore(
            server_id="server3", axis_name="trust",
            p_top=0.95, p_critical=0.8, p_danger=0.6, scored_at=yesterday
        ))

        session.add(McpLlmAxisScore(
            server_id="server1", axis_name="trust",
            p_top=0.7, p_critical=0.5, p_danger=0.3, scored_at=today
        ))
        session.add(McpLlmAxisScore(
            server_id="server2", axis_name="trust",
            p_top=0.85, p_critical=0.65, p_danger=0.45, scored_at=today
        ))
        session.add(McpLlmAxisScore(
            server_id="server3", axis_name="trust",
            p_top=0.92, p_critical=0.75, p_danger=0.55, scored_at=today
        ))

        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier_calibration?days=2")

    # Expected thresholds (approximate based on sample data)
    expected = {
        "days": 2,
        "calibration": {
            "LOW": {"p_top": 0.8, "p_critical": 0.6, "p_danger": 0.4},
            "MEDIUM": {"p_top": 0.9, "p_critical": 0.7, "p_danger": 0.5},
            "HIGH": {"p_top": 0.95, "p_critical": 0.8, "p_danger": 0.6}
        }
    }

    assert response.json() == expected
    print("PASS")