from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from app.db import get_session
from app.models import MCPServerRegistry, MCPRiskRegister
from sqlalchemy.orm import Session
from sqlalchemy import desc

router = APIRouter()

class RiskTierChange(BaseModel):
    timestamp: datetime
    old_tier: str
    new_tier: str
    reason: Optional[str]

@router.get("/risk-tier-changes", response_model=List[RiskTierChange])
async def get_risk_tier_changes(server_id: int, db: Session = Depends(get_session)):
    server = db.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    changes = (
        db.query(MCPRiskRegister)
        .filter(MCPRiskRegister.server_id == server_id)
        .order_by(desc(MCPRiskRegister.timestamp))
        .all()
    )

    result = []
    for i in range(1, len(changes)):
        old = changes[i]
        new = changes[i-1]
        if old.risk_tier != new.risk_tier:
            result.append({
                "timestamp": new.timestamp,
                "old_tier": old.risk_tier,
                "new_tier": new.risk_tier,
                "reason": new.reason if new.reason else None
            })

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    test_client = TestClient(app)

    with SessionLocal() as session:
        server = MCPServerRegistry(id=1, name="Test Server")
        session.add(server)
        session.commit()

        session.add(MCPRiskRegister(
            server_id=1,
            risk_tier="Low",
            timestamp=datetime(2023, 1, 1),
            reason=None
        ))
        session.add(MCPRiskRegister(
            server_id=1,
            risk_tier="Medium",
            timestamp=datetime(2023, 1, 2),
            reason="Increased activity"
        ))
        session.add(MCPRiskRegister(
            server_id=1,
            risk_tier="High",
            timestamp=datetime(2023, 1, 3),
            reason="Security incident"
        ))
        session.commit()

    response = test_client.get("/risk-tier-changes?server_id=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["old_tier"] == "Medium"
    assert data[0]["new_tier"] == "High"
    assert data[0]["reason"] == "Security incident"
    assert data[1]["old_tier"] == "Low"
    assert data[1]["new_tier"] == "Medium"
    assert data[1]["reason"] == "Increased activity"

    print("PASS")