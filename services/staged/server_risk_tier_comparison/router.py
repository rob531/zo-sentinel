from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/servers", tags=["servers"])


class ComparisonResponse(BaseModel):
    server_id1: str
    risk_tier1: Optional[str]
    server_id2: str
    risk_tier2: Optional[str]
    comparison_result: str


@router.get("/compare", response_model=ComparisonResponse)
def compare_servers(
    server_id1: str,
    server_id2: str,
    session: Session = Depends(get_session)
):
    server1 = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id1
    ).first()
    server2 = session.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id2
    ).first()

    if not server1:
        raise HTTPException(status_code=404, detail=f"Server {server_id1} not found")
    if not server2:
        raise HTTPException(status_code=404, detail=f"Server {server_id2} not found")

    risk_tier1 = server1.risk_tier
    risk_tier2 = server2.risk_tier

    if risk_tier1 == risk_tier2:
        comparison_result = f"Both servers have same risk tier: {risk_tier1}"
    elif risk_tier1 and risk_tier2:
        risk_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        r1 = risk_order.get(risk_tier1.lower(), 0)
        r2 = risk_order.get(risk_tier2.lower(), 0)
        if r1 > r2:
            comparison_result = f"{server_id1} has higher risk tier ({risk_tier1}) than {server_id2} ({risk_tier2})"
        else:
            comparison_result = f"{server_id2} has higher risk tier ({risk_tier2}) than {server_id1} ({risk_tier1})"
    else:
        comparison_result = f"Risk tiers differ: {server_id1}={risk_tier1}, {server_id2}={risk_tier2}"

    return ComparisonResponse(
        server_id1=server_id1,
        risk_tier1=risk_tier1,
        server_id2=server_id2,
        risk_tier2=risk_tier2,
        comparison_result=comparison_result,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    server_id1 = "test-server-001"
    server_id2 = "test-server-002"

    with TestingSessionLocal() as session:
        server1 = McpServerRegistry(
            server_id=server_id1,
            name="Test Server 1",
            risk_tier="low",
            confidence=0.9,
            description="Test server one",
            url="http://test1.local",
            registry_source="test",
        )
        server2 = McpServerRegistry(
            server_id=server_id2,
            name="Test Server 2",
            risk_tier="high",
            confidence=0.8,
            description="Test server two",
            url="http://test2.local",
            registry_source="test",
        )
        session.add(server1)
        session.add(server2)
        session.commit()

    with TestClient(app) as client:
        response = client.get(f"/servers/compare?server_id1={server_id1}&server_id2={server_id2}")
        assert response.status_code == 200
        data = response.json()
        assert data["server_id1"] == server_id1
        assert data["server_id2"] == server_id2
        assert data["risk_tier1"] == "low"
        assert data["risk_tier2"] == "high"
        assert data["comparison_result"] is not None
        assert len(data["comparison_result"]) > 0
        print("PASS")