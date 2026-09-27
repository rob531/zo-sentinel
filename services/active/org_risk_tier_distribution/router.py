# deps: fastapi, pydantic, sqlalchemy
"""router.py -- HTTP surface for org_risk_tier_distribution."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import org_risk_tier_distribution

router = APIRouter(prefix="/api", tags=["org_risk_tier_distribution"])


class OrgRiskTierItem(BaseModel):
    org_id: str
    risk_tier: str
    count: int


class OrgRiskTierDistributionResponse(BaseModel):
    items: list[OrgRiskTierItem]
    total_servers: int


@router.get("/orgs/risk-tier-distribution", response_model=OrgRiskTierDistributionResponse)
def get_org_risk_tier_distribution(
    db: Session = Depends(get_session),
) -> OrgRiskTierDistributionResponse:
    """Return count of servers per risk tier grouped by organization."""
    result = org_risk_tier_distribution(db)
    rows = result["rows"]
    items = [
        OrgRiskTierItem(
            org_id=r[0] or "unknown",
            risk_tier=r[1] or "unknown",
            count=r[2],
        )
        for r in rows
    ]
    total = sum(r[2] for r in rows)
    return OrgRiskTierDistributionResponse(items=items, total_servers=total)


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestSessionLocal() as db:
        db.add(McpServerRegistry(server_id="s1", org_id="org1", risk_tier="low"))
        db.add(McpServerRegistry(server_id="s2", org_id="org1", risk_tier="low"))
        db.add(McpServerRegistry(server_id="s3", org_id="org1", risk_tier="high"))
        db.add(McpServerRegistry(server_id="s4", org_id="org2", risk_tier="medium"))
        db.add(McpServerRegistry(server_id="s5", org_id="org3", risk_tier=None))
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/orgs/risk-tier-distribution")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_servers"] == 5, data

    by_org = {}
    for item in data["items"]:
        by_org.setdefault(item["org_id"], {})[item["risk_tier"]] = item["count"]

    assert by_org.get("org1", {}).get("low") == 2, by_org
    assert by_org.get("org1", {}).get("high") == 1, by_org
    assert by_org.get("org2", {}).get("medium") == 1, by_org
    assert by_org.get("org3", {}).get("unknown") == 1, by_org

    print("PASS")
    sys.exit(0)
