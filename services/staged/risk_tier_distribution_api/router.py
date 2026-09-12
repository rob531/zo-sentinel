from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class RiskDistributionResponse(BaseModel):
    tier_counts: dict
    total_servers: int


@router.get("/risk/distribution", response_model=RiskDistributionResponse)
async def get_risk_distribution(session: Session = Depends(get_session)):
    results = (
        session.query(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    tier_counts = {tier: count for tier, count in results}
    total_servers = sum(tier_counts.values())
    return RiskDistributionResponse(tier_counts=tier_counts, total_servers=total_servers)


if __name__ == "__main__":
    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    session = TestingSessionLocal()
    session.add(McpServerRegistry(server_id="s1", name="Server1", risk_tier="critical", registry_source="test", url="http://test1.com"))
    session.add(McpServerRegistry(server_id="s2", name="Server2", risk_tier="critical", registry_source="test", url="http://test2.com"))
    session.add(McpServerRegistry(server_id="s3", name="Server3", risk_tier="high", registry_source="test", url="http://test3.com"))
    session.add(McpServerRegistry(server_id="s4", name="Server4", risk_tier="medium", registry_source="test", url="http://test4.com"))
    session.add(McpServerRegistry(server_id="s5", name="Server5", risk_tier="medium", registry_source="test", url="http://test5.com"))
    session.add(McpServerRegistry(server_id="s6", name="Server6", risk_tier="medium", registry_source="test", url="http://test6.com"))
    session.add(McpServerRegistry(server_id="s7", name="Server7", risk_tier="low", registry_source="test", url="http://test7.com"))
    session.add(McpServerRegistry(server_id="s8", name="Server8", risk_tier="low", registry_source="test", url="http://test8.com"))
    session.add(McpServerRegistry(server_id="s9", name="Server9", risk_tier="low", registry_source="test", url="http://test9.com"))
    session.add(McpServerRegistry(server_id="s10", name="Server10", risk_tier="low", registry_source="test", url="http://test10.com"))
    session.add(McpServerRegistry(server_id="s11", name="Server11", risk_tier="minimal", registry_source="test", url="http://test11.com"))
    session.add(McpServerRegistry(server_id="s12", name="Server12", risk_tier="minimal", registry_source="test", url="http://test12.com"))
    session.add(McpServerRegistry(server_id="s13", name="Server13", risk_tier="minimal", registry_source="test", url="http://test13.com"))
    session.add(McpServerRegistry(server_id="s14", name="Server14", risk_tier="none", registry_source="test", url="http://test14.com"))
    session.add(McpServerRegistry(server_id="s15", name="Server15", risk_tier="none", registry_source="test", url="http://test15.com"))
    session.add(McpServerRegistry(server_id="s16", name="Server16", risk_tier="none", registry_source="test", url="http://test16.com"))
    session.commit()
    session.close()

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    response = client.get("/risk/distribution")
    assert response.status_code == 200
    data = response.json()
    assert data["total_servers"] == 16
    assert data["tier_counts"]["critical"] == 2
    assert data["tier_counts"]["high"] == 1
    assert data["tier_counts"]["medium"] == 3
    assert data["tier_counts"]["low"] == 4
    assert data["tier_counts"]["minimal"] == 3
    assert data["tier_counts"]["none"] == 3
    print("PASS")