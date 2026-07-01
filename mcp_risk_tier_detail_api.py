# deps: fastapi, pydantic, sqlalchemy, sqlmodel

from datetime import datetime
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlmodel import SQLModel

from app.db import get_session
from app.models import McpRiskTier

router = APIRouter()

class RiskTierDetail(BaseModel):
    risk_tier: str
    description: str
    criteria: str
    last_updated: str

@router.get("/mcp-risk-tier/{risk_tier}", response_model=RiskTierDetail)
async def read_risk_tier(risk_tier: str, db: Session = Depends(get_session)):
    risk_tier_detail = db.query(McpRiskTier).filter(McpRiskTier.risk_tier == risk_tier).first()
    if risk_tier_detail is None:
        raise HTTPException(status_code=404, detail="Risk tier not found")
    return RiskTierDetail(
        risk_tier=risk_tier_detail.risk_tier,
        description=risk_tier_detail.description,
        criteria=risk_tier_detail.criteria,
        last_updated=_norm(risk_tier_detail.last_updated)
    )

def _norm(dt_str: str) -> str:
    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    return dt.isoformat()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_db

    client = TestClient(app)

    # Create tables
    SQLModel.metadata.create_all(bind=engine)

    # Seed data
    db = TestingSessionLocal()
    db.add(McpRiskTier(
        risk_tier="HIGH",
        description="High risk tier",
        criteria="High criteria",
        last_updated="2023-01-01 00:00:00"
    ))
    db.commit()
    db.close()

    # Test the endpoint
    response = client.get("/mcp-risk-tier/HIGH")
    assert response.status_code == 200
    assert response.json() == {
        "risk_tier": "HIGH",
        "description": "High risk tier",
        "criteria": "High criteria",
        "last_updated": "2023-01-01T00:00:00"
    }

    print("PASS")