from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api")

def get_server_risk_history(server_id: str, days: int = 30, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    # Get risk tier history from McpServerRegistry
    tier_history = session.query(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
        McpServerRegistry.last_assessed
    ).filter(
        McpServerRegistry.server_id == server_id,
        McpServerRegistry.last_assessed >= start_date
    ).order_by(
        McpServerRegistry.last_assessed.asc()
    ).all()

    # Get overall risk scores from McpLlmAxisScore
    risk_scores = session.query(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.overall_risk
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start_date
    ).order_by(
        McpLlmAxisScore.scored_at.asc()
    ).all()

    # Combine and deduplicate by date
    combined = {}
    for entry in tier_history:
        date = entry.last_assessed.date()
        if date not in combined:
            combined[date] = {"date": date.isoformat(), "risk_tier": None, "overall_risk": None}
        combined[date]["risk_tier"] = entry.risk_tier

    for entry in risk_scores:
        date = entry.scored_at.date()
        if date not in combined:
            combined[date] = {"date": date.isoformat(), "risk_tier": None, "overall_risk": None}
        combined[date]["overall_risk"] = entry.overall_risk

    # Convert to list and sort by date
    history = list(combined.values())
    history.sort(key=lambda x: x["date"])

    return history

@router.get("/server/{server_id}/risk_history")
async def server_risk_history(
    server_id: str,
    days: int = 30,
    session: Session = Depends(get_session)
):
    history = get_server_risk_history(server_id, days, session)

    return {
        "server_id": server_id,
        "days": days,
        "history": history
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override get_session for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Insert test data
    with SessionLocal() as session:
        session.add(McpServerRegistry(
            server_id="test-server-1",
            risk_tier="high",
            last_assessed=datetime.utcnow() - timedelta(days=5)
        ))
        session.add(McpServerRegistry(
            server_id="test-server-1",
            risk_tier="medium",
            last_assessed=datetime.utcnow() - timedelta(days=2)
        ))
        session.add(McpServerRegistry(
            server_id="test-server-1",
            risk_tier="low",
            last_assessed=datetime.utcnow()
        ))
        session.add(McpLlmAxisScore(
            server_id="test-server-1",
            scored_at=datetime.utcnow() - timedelta(days=5),
            overall_risk=0.8
        ))
        session.add(McpLlmAxisScore(
            server_id="test-server-1",
            scored_at=datetime.utcnow() - timedelta(days=2),
            overall_risk=0.5
        ))
        session.add(McpLlmAxisScore(
            server_id="test-server-1",
            scored_at=datetime.utcnow(),
            overall_risk=0.2
        ))
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/server/test-server-1/risk_history?days=10")
    assert response.status_code == 200
    data = response.json()
    assert len(data["history"]) == 3
    assert data["history"][0]["risk_tier"] == "high"
    print("PASS")