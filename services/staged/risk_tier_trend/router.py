from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/risk")

def get_risk_tier(score: float) -> str:
    if score >= 0.9:
        return "high"
    elif score >= 0.7:
        return "medium"
    elif score >= 0.5:
        return "low"
    else:
        return "minimal"

def get_risk_tier_trend(server_id: str, days: int, session: Session) -> Dict:
    if days > 30:
        raise HTTPException(status_code=400, detail="days must be <= 30")

    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    # Get server name
    server = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Get scores for the date range
    scores = session.query(
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.p_top
    ).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == 'overall_risk',
        McpLlmAxisScore.scored_at >= start_date,
        McpLlmAxisScore.scored_at <= end_date
    ).all()

    # Group by date and calculate tier counts
    date_to_tier_counts = {}
    for scored_at, p_top in scores:
        date = scored_at.date()
        tier = get_risk_tier(p_top)
        if date not in date_to_tier_counts:
            date_to_tier_counts[date] = {}
        date_to_tier_counts[date][tier] = date_to_tier_counts[date].get(tier, 0) + 1

    # Convert to the required output format
    series = []
    for date, tier_counts in date_to_tier_counts.items():
        for tier, count in tier_counts.items():
            series.append({
                "date": date.isoformat(),
                "tier": tier,
                "count": count
            })

    return {
        "server_id": server_id,
        "days": days,
        "series": series
    }

@router.get("/tier_trend")
async def tier_trend(
    server_id: str,
    days: int,
    session: Session = Depends(get_session)
) -> Dict:
    return get_risk_tier_trend(server_id, days, session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    session = SessionLocal()
    try:
        # Add test servers
        session.add(McpServerRegistry(server_id="test1", name="Test Server 1"))
        session.add(McpServerRegistry(server_id="test2", name="Test Server 2"))

        # Add test scores
        today = datetime.utcnow().date()
        yesterday = today - timedelta(days=1)

        session.add(McpLlmAxisScore(
            server_id="test1",
            axis_name="overall_risk",
            p_top=0.95,
            scored_at=datetime.combine(today, datetime.min.time())
        ))
        session.add(McpLlmAxisScore(
            server_id="test1",
            axis_name="overall_risk",
            p_top=0.85,
            scored_at=datetime.combine(yesterday, datetime.min.time())
        ))
        session.add(McpLlmAxisScore(
            server_id="test2",
            axis_name="overall_risk",
            p_top=0.65,
            scored_at=datetime.combine(today, datetime.min.time())
        ))

        session.commit()

        # Test the endpoint
        result = get_risk_tier_trend("test1", 2, session)
        assert len(result["series"]) == 2
        assert result["series"][0]["tier"] == "high"
        assert result["series"][1]["tier"] == "medium"
        print("PASS")
    finally:
        session.close()