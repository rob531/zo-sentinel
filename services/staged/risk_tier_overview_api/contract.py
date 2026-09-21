from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from pydantic import BaseModel
from typing import Dict

from app.db import get_session
from app.models import McpServerRegistry, Base


class RiskOverviewResponse(BaseModel):
    total_servers: int
    by_tier: Dict[str, int]


def get_risk_overview_data(session: Session) -> Dict:
    total = session.query(func.count(McpServerRegistry.server_id)).scalar()
    
    tier_counts = session.query(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id)
    ).group_by(McpServerRegistry.risk_tier).all()
    
    by_tier = {tier: count for tier, count in tier_counts}
    
    return {"total_servers": total, "by_tier": by_tier}


app = FastAPI()


@app.get("/api/risk/overview", response_model=RiskOverviewResponse)
def risk_overview_endpoint(session: Session = Depends(get_session)):
    return get_risk_overview_data(session)


def run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal() as session:
        session.add(McpServerRegistry(server_id="srv_1", name="Server 1", risk_tier="low", url="http://1.local"))
        session.add(McpServerRegistry(server_id="srv_2", name="Server 2", risk_tier="medium", url="http://2.local"))
        session.add(McpServerRegistry(server_id="srv_3", name="Server 3", risk_tier="high", url="http://3.local"))
        session.add(McpServerRegistry(server_id="srv_4", name="Server 4", risk_tier="critical", url="http://4.local"))
        session.add(McpServerRegistry(server_id="srv_5", name="Server 5", risk_tier="low", url="http://5.local"))
        session.add(McpServerRegistry(server_id="srv_6", name="Server 6", risk_tier="medium", url="http://6.local"))
        session.add(McpServerRegistry(server_id="srv_7", name="Server 7", risk_tier="high", url="http://7.local"))
        session.add(McpServerRegistry(server_id="srv_8", name="Server 8", risk_tier="low", url="http://8.local"))
        session.add(McpServerRegistry(server_id="srv_9", name="Server 9", risk_tier="critical", url="http://9.local"))
        session.add(McpServerRegistry(server_id="srv_10", name="Server 10", risk_tier="medium", url="http://10.local"))
        session.commit()

    def override_get_session():
        return TestingSessionLocal()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/risk/overview")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert data["total_servers"] == 10, f"Expected 10 servers, got {data['total_servers']}"
    assert data["by_tier"]["low"] == 3, f"Expected 3 low tier servers, got {data['by_tier'].get('low')}"

    print("PASS")


if __name__ == "__main__":
    run_self_test()