from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry
from pydantic import BaseModel
from typing import Dict, List
from collections import defaultdict

router = APIRouter()

class TierComparison(BaseModel):
    group: Dict[str, Dict[str, int]]

@router.get("/risk-tier-comparison", response_model=TierComparison)
def get_risk_tier_comparison(session: Session = Depends(get_session)):
    query = session.query(MCPServerRegistry.group, MCPServerRegistry.risk_tier).all()
    tier_counts = defaultdict(lambda: defaultdict(int))
    for group, tier in query:
        tier_counts[group][tier] += 1

    result = {}
    for group, tiers in tier_counts.items():
        sorted_tiers = sorted(tiers.items(), key=lambda x: x[1], reverse=True)[:5]
        result[group] = dict(sorted_tiers)

    return {"group": result}

app = FastAPI()
app.include_router(router)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    test_data = [
        MCPServerRegistry(group="group1", risk_tier="tier1"),
        MCPServerRegistry(group="group1", risk_tier="tier2"),
        MCPServerRegistry(group="group1", risk_tier="tier3"),
        MCPServerRegistry(group="group1", risk_tier="tier4"),
        MCPServerRegistry(group="group1", risk_tier="tier5"),
        MCPServerRegistry(group="group1", risk_tier="tier6"),
        MCPServerRegistry(group="group1", risk_tier="tier7"),
        MCPServerRegistry(group="group2", risk_tier="tier1"),
        MCPServerRegistry(group="group2", risk_tier="tier2"),
        MCPServerRegistry(group="group2", risk_tier="tier3"),
        MCPServerRegistry(group="group2", risk_tier="tier4"),
        MCPServerRegistry(group="group2", risk_tier="tier5"),
        MCPServerRegistry(group="group2", risk_tier="tier6"),
        MCPServerRegistry(group="group2", risk_tier="tier7"),
    ]

    db = SessionLocal()
    db.add_all(test_data)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/risk-tier-comparison")
    assert response.status_code == 200
    data = response.json()
    assert len(data["group"]["group1"]) == 5
    assert len(data["group"]["group2"]) == 5
    print("PASS")