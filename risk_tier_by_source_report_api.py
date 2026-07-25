from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import MCPServerRegistry
from pydantic import BaseModel

router = APIRouter()

class RiskTierCounts(BaseModel):
    source: str
    tier_counts: Dict[str, int]

@router.get("/report/risk-tiers-by-source", response_model=List[RiskTierCounts])
async def get_risk_tiers_by_source(db: Session = Depends(get_session)):
    try:
        # Query the database to get all server registries with their risk tiers
        registries = db.query(
            MCPServerRegistry.source,
            MCPServerRegistry.risk_tier
        ).all()

        # Aggregate the results by source and risk tier
        source_tier_counts = {}
        for source, tier in registries:
            if source not in source_tier_counts:
                source_tier_counts[source] = {}
            if tier not in source_tier_counts[source]:
                source_tier_counts[source][tier] = 0
            source_tier_counts[source][tier] += 1

        # Convert the aggregated data to the response model
        result = []
        for source, tiers in source_tier_counts.items():
            result.append(
                RiskTierCounts(
                    source=source,
                    tier_counts=tiers
                )
            )

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import Base, engine
    from app.models import MCPServerRegistry
    from sqlalchemy.orm import Session

    # Override the database session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    app.dependency_overrides[get_session] = lambda: Session(test_engine)

    # Seed test data
    with Session(test_engine) as session:
        session.add_all([
            MCPServerRegistry(
                source="source1",
                risk_tier="low"
            ),
            MCPServerRegistry(
                source="source1",
                risk_tier="medium"
            ),
            MCPServerRegistry(
                source="source1",
                risk_tier="high"
            ),
            MCPServerRegistry(
                source="source2",
                risk_tier="low"
            ),
            MCPServerRegistry(
                source="source2",
                risk_tier="low"
            ),
            MCPServerRegistry(
                source="source2",
                risk_tier="medium"
            ),
        ])
        session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/report/risk-tiers-by-source")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    source1 = next(item for item in data if item["source"] == "source1")
    assert source1["tier_counts"] == {"low": 1, "medium": 1, "high": 1}

    source2 = next(item for item in data if item["source"] == "source2")
    assert source2["tier_counts"] == {"low": 2, "medium": 1}

    print("PASS")