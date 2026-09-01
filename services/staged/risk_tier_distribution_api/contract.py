from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class RiskDistributionResponse(BaseModel):
    tier_counts: dict[str, int]
    total_servers: int


@router.get("/risk/distribution", response_model=RiskDistributionResponse)
def get_risk_distribution(db: Session = Depends(get_session)) -> dict[str, Any]:
    results = (
        db.query(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.risk_tier.isnot(None))
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    tier_counts = {tier: count for tier, count in results}
    total_servers = sum(tier_counts.values())
    return RiskDistributionResponse(tier_counts=tier_counts, total_servers=total_servers)


if __name__ == "__main__":
    import sys

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    McpServerRegistry.metadata.create_all(test_engine)
    db = TestingSessionLocal()
    test_data = [
        {"server_id": "s1", "risk_tier": "tier_1"},
        {"server_id": "s2", "risk_tier": "tier_1"},
        {"server_id": "s3", "risk_tier": "tier_2"},
        {"server_id": "s4", "risk_tier": "tier_2"},
        {"server_id": "s5", "risk_tier": "tier_2"},
        {"server_id": "s6", "risk_tier": "tier_3"},
        {"server_id": "s7", "risk_tier": "tier_4"},
        {"server_id": "s8", "risk_tier": "tier_4"},
        {"server_id": "s9", "risk_tier": "tier_5"},
        {"server_id": "s10", "risk_tier": "tier_5"},
        {"server_id": "s11", "risk_tier": "tier_5"},
        {"server_id": "s12", "risk_tier": "tier_6"},
        {"server_id": "s13", "risk_tier": "tier_6"},
    ]
    for row in test_data:
        db.add(McpServerRegistry(**row))
    db.commit()
    db.close()

    with TestClient(app) as client:
        response = client.get("/risk/distribution")
        assert response.status_code == 200
        data = response.json()
        assert len(data["tier_counts"]) == 6
        assert data["total_servers"] == 13
        print("PASS")
        sys.exit(0)