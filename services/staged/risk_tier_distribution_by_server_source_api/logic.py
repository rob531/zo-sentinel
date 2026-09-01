from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Dict
from app.db import get_session
from app.models import McpServerRegistry


class RiskTierDistributionResponse(BaseModel):
    class Config:
        from_attributes = True


def get_risk_tier_distribution(
    session: Session = Depends(get_session),
) -> Dict[str, Dict[str, int]]:
    results = (
        session.query(
            McpServerRegistry.registry_source,
            McpServerRegistry.risk_tier,
        )
        .filter(
            McpServerRegistry.registry_source.isnot(None),
            McpServerRegistry.risk_tier.isnot(None),
        )
        .all()
    )
    
    distribution: Dict[str, Dict[str, int]] = {}
    for source, tier in results:
        if source not in distribution:
            distribution[source] = {}
        distribution[source][tier] = distribution[source].get(tier, 0) + 1
    
    return distribution


if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import Base
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    Base.metadata.create_all(bind=engine)
    
    session = TestingSessionLocal()
    
    test_data = [
        {"server_id": "srv1", "name": "Server 1", "url": "https://example.com/1", "registry_source": "github", "risk_tier": "low"},
        {"server_id": "srv2", "name": "Server 2", "url": "https://example.com/2", "registry_source": "github", "risk_tier": "high"},
        {"server_id": "srv3", "name": "Server 3", "url": "https://example.com/3", "registry_source": "huggingface", "risk_tier": "low"},
        {"server_id": "srv4", "name": "Server 4", "url": "https://example.com/4", "registry_source": "huggingface", "risk_tier": "high"},
        {"server_id": "srv5", "name": "Server 5", "url": "https://example.com/5", "registry_source": "custom", "risk_tier": "low"},
    ]
    
    for data in test_data:
        session.add(McpServerRegistry(**data))
    session.commit()
    
    app = FastAPI()
    
    def override_get_session():
        yield session
    
    app.dependency_overrides[get_session] = override_get_session
    
    result = get_risk_tier_distribution(session=session)
    
    assert len(result) == 3, f"Expected 3 sources, got {len(result)}"
    assert "github" in result
    assert "huggingface" in result
    assert "custom" in result
    
    print("PASS")