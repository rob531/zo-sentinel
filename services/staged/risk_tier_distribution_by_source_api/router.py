from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Dict, List
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

class RiskTierDistribution(BaseModel):
    distribution: Dict[str, int]

class SourceDistribution(BaseModel):
    source: str
    distribution: Dict[str, int]

@router.get("/risk/distribution", response_model=List[SourceDistribution])
def get_risk_tier_distribution_by_source(source: str = None, session: Session = Depends(get_session)):
    query = session.query(
        McpServerRegistry.registry_source,
        McpServerRegistry.risk_tier
    ).filter(McpServerRegistry.risk_tier.isnot(None))

    if source:
        query = query.filter(McpServerRegistry.registry_source == source)

    results = query.all()

    distribution = {}
    for result in results:
        registry_source = result.registry_source
        risk_tier = result.risk_tier

        if registry_source not in distribution:
            distribution[registry_source] = {}

        if risk_tier not in distribution[registry_source]:
            distribution[registry_source][risk_tier] = 0

        distribution[registry_source][risk_tier] += 1

    response = []
    for source, tiers in distribution.items():
        response.append({
            "source": source,
            "distribution": tiers
        })

    return response

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create an in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create test data
    from app.models import McpServerRegistry
    test_data = [
        McpServerRegistry(
            registry_source="source1",
            risk_tier="low",
            server_id="server1"
        ),
        McpServerRegistry(
            registry_source="source1",
            risk_tier="medium",
            server_id="server2"
        ),
        McpServerRegistry(
            registry_source="source1",
            risk_tier="medium",
            server_id="server3"
        ),
        McpServerRegistry(
            registry_source="source2",
            risk_tier="high",
            server_id="server4"
        ),
        McpServerRegistry(
            registry_source="source2",
            risk_tier="high",
            server_id="server5"
        ),
        McpServerRegistry(
            registry_source="source3",
            risk_tier="low",
            server_id="server6"
        )
    ]

    session = SessionLocal()
    session.add_all(test_data)
    session.commit()

    # Override the get_session dependency for testing
    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        return SessionLocal()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/risk/distribution")
    assert response.status_code == 200
    assert len(response.json()) == 3

    # Test filtering by source
    response = client.get("/risk/distribution?source=source1")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["source"] == "source1"
    assert response.json()[0]["distribution"] == {"low": 1, "medium": 2}

    print("PASS")