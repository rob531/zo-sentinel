from typing import List
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

class RiskTierSummary(BaseModel):
    total_servers: int
    tiers: List[dict]

def get_risk_tier_overview(db: Session = Depends(get_session)) -> RiskTierSummary:
    # Query server registry for all servers with their risk tiers
    servers = db.query(McpServerRegistry.server_id, McpServerRegistry.risk_tier).all()

    # Initialize tier counts
    tier_counts = {
        'TRUSTED_GENERAL': 0,
        'TRUSTED_RESEARCH': 0,
        'ENTERPRISE_CONTROLLED': 0,
        'CAUTION_LIMITED': 0,
        'HIGH_RISK_ISOLATED': 0,
        'KNOWN_THREAT': 0,
        'INSUFFICIENT': 0
    }

    # Count servers per tier
    for server in servers:
        tier = server.risk_tier
        if tier in tier_counts:
            tier_counts[tier] += 1

    # Calculate total servers
    total_servers = sum(tier_counts.values())

    # Calculate percentages
    tiers = []
    for tier, count in tier_counts.items():
        percentage = (count / total_servers) * 100 if total_servers > 0 else 0.0
        tiers.append({
            'tier': tier,
            'count': count,
            'percentage': round(percentage, 2)
        })

    return RiskTierSummary(total_servers=total_servers, tiers=tiers)

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

    # Seed test data
    def seed_test_data():
        session = SessionLocal()
        try:
            # Seed McpServerRegistry with test data
            test_servers = [
                McpServerRegistry(server_id=f"server_{i}", name=f"Test Server {i}", risk_tier=tier)
                for i, tier in enumerate([
                    'TRUSTED_GENERAL', 'TRUSTED_GENERAL',
                    'TRUSTED_RESEARCH', 'TRUSTED_RESEARCH',
                    'ENTERPRISE_CONTROLLED', 'ENTERPRISE_CONTROLLED',
                    'CAUTION_LIMITED', 'CAUTION_LIMITED',
                    'HIGH_RISK_ISOLATED', 'HIGH_RISK_ISOLATED',
                    'KNOWN_THREAT', 'KNOWN_THREAT',
                    'INSUFFICIENT', 'INSUFFICIENT'
                ])
            ]
            session.add_all(test_servers)
            session.commit()
        finally:
            session.close()

    seed_test_data()

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create FastAPI app and test client
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/risk/overview")
    assert response.status_code == 200
    data = response.json()

    assert data["total_servers"] == 14
    for tier in data["tiers"]:
        assert tier["count"] == 2

    print("PASS")