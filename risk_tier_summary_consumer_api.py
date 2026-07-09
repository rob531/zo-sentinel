from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPServerRegistry, MCPAxisScores

router = APIRouter()

class RiskTierSummary(BaseModel):
    tier: str
    count: int
    pct: float
    avg_confidence: float
    top_risk_axes: List[str]

class RiskTierSummaryResponse(BaseModel):
    tiers: List[RiskTierSummary]

def calculate_risk_tier_summary(session: Session) -> RiskTierSummaryResponse:
    # Get all servers with their risk tiers and confidence
    servers = session.query(
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.confidence
    ).all()

    # Group by risk tier
    tier_data = {}
    for tier, confidence in servers:
        if tier not in tier_data:
            tier_data[tier] = {'count': 0, 'total_confidence': 0, 'axes': {}}
        tier_data[tier]['count'] += 1
        tier_data[tier]['total_confidence'] += confidence

    # Get all axis scores
    axis_scores = session.query(
        MCPAxisScores.server_id,
        MCPAxisScores.axis_name,
        MCPAxisScores.p_top,
        MCPAxisScores.p_critical,
        MCPAxisScores.p_danger
    ).all()

    # Calculate top risk axes for each tier
    for server_id, axis_name, p_top, p_critical, p_danger in axis_scores:
        server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
        if server and server.risk_tier:
            tier = server.risk_tier
            if tier in tier_data:
                risk_score = p_top + p_critical + p_danger
                if axis_name not in tier_data[tier]['axes']:
                    tier_data[tier]['axes'][axis_name] = 0
                tier_data[tier]['axes'][axis_name] += risk_score

    # Prepare response
    tiers = []
    total_servers = sum(data['count'] for data in tier_data.values())

    for tier, data in tier_data.items():
        avg_confidence = data['total_confidence'] / data['count'] if data['count'] > 0 else 0
        pct = (data['count'] / total_servers) * 100 if total_servers > 0 else 0

        # Get top 3 risk axes
        top_axes = sorted(data['axes'].items(), key=lambda x: x[1], reverse=True)[:3]
        top_axes_names = [axis[0] for axis in top_axes]

        tiers.append(RiskTierSummary(
            tier=tier,
            count=data['count'],
            pct=pct,
            avg_confidence=avg_confidence,
            top_risk_axes=top_axes_names
        ))

    return RiskTierSummaryResponse(tiers=tiers)

@router.get("/dashboard/risk-tier-summary", response_model=RiskTierSummaryResponse)
async def get_risk_tier_summary(session: Session = Depends(get_session)):
    return calculate_risk_tier_summary(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Seed test data
    def seed_test_data():
        session = SessionLocal()
        try:
            # Add test servers
            servers = [
                MCPServerRegistry(server_id="s1", risk_tier="low", confidence=0.9),
                MCPServerRegistry(server_id="s2", risk_tier="low", confidence=0.85),
                MCPServerRegistry(server_id="s3", risk_tier="medium", confidence=0.7),
                MCPServerRegistry(server_id="s4", risk_tier="medium", confidence=0.65),
                MCPServerRegistry(server_id="s5", risk_tier="high", confidence=0.5),
                MCPServerRegistry(server_id="s6", risk_tier="high", confidence=0.45)
            ]
            session.add_all(servers)

            # Add test axis scores
            axes = [
                MCPAxisScores(server_id="s1", axis_name="axis1", p_top=0.1, p_critical=0.2, p_danger=0.3),
                MCPAxisScores(server_id="s1", axis_name="axis2", p_top=0.15, p_critical=0.25, p_danger=0.35),
                MCPAxisScores(server_id="s2", axis_name="axis1", p_top=0.2, p_critical=0.3, p_danger=0.4),
                MCPAxisScores(server_id="s2", axis_name="axis3", p_top=0.25, p_critical=0.35, p_danger=0.45),
                MCPAxisScores(server_id="s3", axis_name="axis2", p_top=0.3, p_critical=0.4, p_danger=0.5),
                MCPAxisScores(server_id="s3", axis_name="axis3", p_top=0.35, p_critical=0.45, p_danger=0.55),
                MCPAxisScores(server_id="s4", axis_name="axis1", p_top=0.4, p_critical=0.5, p_danger=0.6),
                MCPAxisScores(server_id="s4", axis_name="axis4", p_top=0.45, p_critical=0.55, p_danger=0.65),
                MCPAxisScores(server_id="s5", axis_name="axis2", p_top=0.5, p_critical=0.6, p_danger=0.7),
                MCPAxisScores(server_id="s5", axis_name="axis4", p_top=0.55, p_critical=0.65, p_danger=0.75),
                MCPAxisScores(server_id="s6", axis_name="axis3", p_top=0.6, p_critical=0.7, p_danger=0.8),
                MCPAxisScores(server_id="s6", axis_name="axis4", p_top=0.65, p_critical=0.75, p_danger=0.85)
            ]
            session.add_all(axes)
            session.commit()
        finally:
            session.close()

    seed_test_data()

    # Override dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/dashboard/risk-tier-summary")
    assert response.status_code == 200
    data = response.json()

    # Verify response
    assert len(data["tiers"]) == 3
    total_pct = sum(tier["pct"] for tier in data["tiers"])
    assert abs(total_pct - 100) < 0.01  # Allow for floating point rounding

    print("PASS")