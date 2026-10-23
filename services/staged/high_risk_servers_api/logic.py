"""
High-risk servers API service.
"""
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class HighRiskServerResponse(BaseModel):
    server_id: str
    name: str
    url: str
    risk_tier: str
    last_assessed: Optional[datetime] = None

    class Config:
        from_attributes = True


@router.get("/api/risk/high-risk-servers", response_model=List[HighRiskServerResponse])
def get_high_risk_servers(session: Session = Depends(get_session)) -> List[HighRiskServerResponse]:
    """
    Retrieve servers with HIGH_RISK_ISOLATED or KNOWN_THREAT risk tier.
    """
    servers = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.risk_tier.in_(["HIGH_RISK_ISOLATED", "KNOWN_THREAT"]))
        .all()
    )
    
    return [
        HighRiskServerResponse(
            server_id=s.server_id,
            name=s.name,
            url=s.url,
            risk_tier=s.risk_tier,
            last_assessed=s.last_assessed
        )
        for s in servers
    ]


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    TestingSessionLocal = sessionmaker(bind=engine)
    
    McpServerRegistry.metadata.create_all(bind=engine)
    
    db = TestingSessionLocal()
    try:
        db.add(McpServerRegistry(
            server_id=str(uuid4()),
            name="Threat Server",
            url="https://threat.example.com",
            risk_tier="HIGH_RISK_ISOLATED",
            last_assessed=datetime.utcnow(),
        ))
        db.add(McpServerRegistry(
            server_id=str(uuid4()),
            name="Known Bad Actor",
            url="https://known-bad.example.com",
            risk_tier="KNOWN_THREAT",
            last_assessed=datetime.utcnow(),
        ))
        db.add(McpServerRegistry(
            server_id=str(uuid4()),
            name="Safe Server",
            url="https://safe.example.com",
            risk_tier="SAFE",
            last_assessed=datetime.utcnow(),
        ))
        db.commit()
    finally:
        db.close()
    
    that_app = FastAPI()
    that_app.include_router(router)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    that_app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(that_app)
    resp = client.get("/api/risk/high-risk-servers")
    
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    
    assert len(data) == 2, f"Expected 2 high-risk servers, got {len(data)}"
    
    risk_tiers = {item["risk_tier"] for item in data}
    assert risk_tiers == {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}, f"Unexpected risk tiers: {risk_tiers}"
    
    print("PASS")