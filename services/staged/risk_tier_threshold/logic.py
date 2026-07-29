from fastapi import Depends, HTTPException
from sqlalchemy import func, and_
from sqlalchemy.orm import Session
from typing import Dict, Any
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

def get_risk_tier_thresholds(days: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """
    Calculate percentile thresholds for each risk tier based on p_top scores
    from the last N days.

    Args:
        days: Number of days to look back for scores
        session: SQLAlchemy session

    Returns:
        Dictionary with thresholds and counts for each risk tier
    """
    # Calculate the date N days ago
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    # Query to get scores with server details
    query = session.query(
        McpLlmAxisScore.p_top,
        McpServerRegistry.risk_tier
    ).join(
        McpServerRegistry,
        McpLlmAxisScore.server_id == McpServerRegistry.id
    ).filter(
        McpLlmAxisScore.scored_at >= cutoff_date
    ).subquery()

    # Calculate percentiles for each risk tier
    thresholds = session.query(
        query.c.risk_tier,
        func.percentile_cont(0.75).within_group(query.c.p_top).label('threshold'),
        func.count().label('count')
    ).group_by(
        query.c.risk_tier
    ).all()

    # Format results
    result = {
        "days": days,
        "thresholds": {},
        "counts": {}
    }

    for tier, threshold, count in thresholds:
        result["thresholds"][tier] = float(threshold)
        result["counts"][tier] = int(count)

    return result

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the dependency for testing
    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Include the router
    from services.staged.risk_tier_threshold import router
    app.include_router(router)

    # Seed test data
    session = SessionLocal()
    try:
        # Create test servers
        servers = [
            McpServerRegistry(
                id=1,
                hostname="server1.example.com",
                risk_tier="LOW"
            ),
            McpServerRegistry(
                id=2,
                hostname="server2.example.com",
                risk_tier="MEDIUM"
            ),
            McpServerRegistry(
                id=3,
                hostname="server3.example.com",
                risk_tier="HIGH"
            ),
            McpServerRegistry(
                id=4,
                hostname="server4.example.com",
                risk_tier="LOW"
            ),
            McpServerRegistry(
                id=5,
                hostname="server5.example.com",
                risk_tier="MEDIUM"
            )
        ]
        session.add_all(servers)

        # Create test scores
        scores = [
            McpLlmAxisScore(
                server_id=1,
                p_top=0.1,
                scored_at=datetime.utcnow() - timedelta(days=1)
            ),
            McpLlmAxisScore(
                server_id=2,
                p_top=0.5,
                scored_at=datetime.utcnow() - timedelta(days=1)
            ),
            McpLlmAxisScore(
                server_id=3,
                p_top=0.9,
                scored_at=datetime.utcnow() - timedelta(days=1)
            ),
            McpLlmAxisScore(
                server_id=4,
                p_top=0.2,
                scored_at=datetime.utcnow() - timedelta(days=1)
            ),
            McpLlmAxisScore(
                server_id=5,
                p_top=0.6,
                scored_at=datetime.utcnow() - timedelta(days=1)
            ),
            McpLlmAxisScore(
                server_id=1,
                p_top=0.15,
                scored_at=datetime.utcnow()
            ),
            McpLlmAxisScore(
                server_id=2,
                p_top=0.55,
                scored_at=datetime.utcnow()
            ),
            McpLlmAxisScore(
                server_id=3,
                p_top=0.95,
                scored_at=datetime.utcnow()
            ),
            McpLlmAxisScore(
                server_id=4,
                p_top=0.25,
                scored_at=datetime.utcnow()
            ),
            McpLlmAxisScore(
                server_id=5,
                p_top=0.65,
                scored_at=datetime.utcnow()
            )
        ]
        session.add_all(scores)
        session.commit()
    finally:
        session.close()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/risk/tier/threshold?days=2")

    assert response.status_code == 200
    data = response.json()
    assert "thresholds" in data
    assert "counts" in data
    assert set(data["thresholds"].keys()) == {"LOW", "MEDIUM", "HIGH"}
    assert data["counts"]["LOW"] == 2
    assert data["counts"]["MEDIUM"] == 2
    assert data["counts"]["HIGH"] == 1

    print("PASS")