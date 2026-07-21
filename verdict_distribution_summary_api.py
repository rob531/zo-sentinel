from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class TierDistribution(BaseModel):
    total_servers: int
    tiers: Dict[str, int]
    generated_at: str

class SourceTierDistribution(BaseModel):
    sources: Dict[str, Dict[str, int]]

@router.get("/verdicts/summary", response_model=TierDistribution)
async def get_verdict_distribution(db: Session = Depends(get_session)):
    # Get all servers with their risk tiers
    servers = db.query(
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.registry_source
    ).all()

    # Calculate tier distribution
    tier_counts = {}
    for server in servers:
        tier = server.risk_tier
        if tier in tier_counts:
            tier_counts[tier] += 1
        else:
            tier_counts[tier] = 1

    # Prepare response
    response = {
        "total_servers": len(servers),
        "tiers": tier_counts,
        "generated_at": datetime.utcnow().isoformat()
    }

    return response

@router.get("/verdicts/by-source", response_model=SourceTierDistribution)
async def get_verdicts_by_source(db: Session = Depends(get_session)):
    # Get all servers with their risk tiers and sources
    servers = db.query(
        MCPServerRegistry.risk_tier,
        MCPServerRegistry.registry_source
    ).all()

    # Calculate tier distribution by source
    source_tier_counts = {}
    for server in servers:
        source = server.registry_source
        tier = server.risk_tier

        if source not in source_tier_counts:
            source_tier_counts[source] = {}

        if tier in source_tier_counts[source]:
            source_tier_counts[source][tier] += 1
        else:
            source_tier_counts[source][tier] = 1

    # Prepare response
    response = {
        "sources": source_tier_counts
    }

    return response

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSessionLocal()

    # Seed test data
    test_db = TestSessionLocal()
    test_db.add_all([
        MCPServerRegistry(
            id=1,
            risk_tier="high",
            registry_source="source1"
        ),
        MCPServerRegistry(
            id=2,
            risk_tier="medium",
            registry_source="source1"
        ),
        MCPServerRegistry(
            id=3,
            risk_tier="low",
            registry_source="source2"
        ),
        MCPServerRegistry(
            id=4,
            risk_tier="high",
            registry_source="source2"
        ),
        MCPServerRegistry(
            id=5,
            risk_tier="high",
            registry_source="source3"
        )
    ])
    test_db.commit()

    # Test client
    client = TestClient(app)

    # Test /verdicts/summary
    response = client.get("/verdicts/summary")
    assert response.status_code == 200
    data = response.json()
    assert "total_servers" in data
    assert "tiers" in data
    assert "generated_at" in data
    assert data["total_servers"] > 0
    assert all(tier in data["tiers"] for tier in ["high", "medium", "low"])

    # Test /verdicts/by-source
    response = client.get("/verdicts/by-source")
    assert response.status_code == 200
    data = response.json()
    assert "sources" in data
    assert len(data["sources"]) > 0

    print("PASS")