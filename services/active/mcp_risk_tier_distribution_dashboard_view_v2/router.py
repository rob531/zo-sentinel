# deps: fastapi, sqlalchemy, pydantic
"""FastAPI router for MCP risk tier distribution dashboard (v2).
Provides a public endpoint that returns the count of servers per risk tier.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_risk_tier_distribution_dashboard_view_v2"])


class RiskTierDistributionItem(BaseModel):
    risk_tier: str
    count: int


@router.get("/risk_tier_distribution", response_model=list[RiskTierDistributionItem])
def get_risk_tier_distribution(db: Session = Depends(get_session)):
    """Return the number of servers for each risk tier."""
    try:
        results = (
            db.query(McpServerRegistry.risk_tier, func.count().label("cnt"))
            .group_by(McpServerRegistry.risk_tier)
            .all()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return [RiskTierDistributionItem(risk_tier=rt, count=cnt) for rt, cnt in results]


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with TestSessionLocal() as db:
        db.add_all([
            McpServerRegistry(server_id="s1", risk_tier="low"),
            McpServerRegistry(server_id="s2", risk_tier="medium"),
            McpServerRegistry(server_id="s3", risk_tier="low"),
        ])
        db.commit()

    client = TestClient(app)
    resp = client.get("/api/risk_tier_distribution")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    expected = {"low": 2, "medium": 1}
    got = {item["risk_tier"]: item["count"] for item in data}
    if expected == got:
        print("PASS")
        sys.exit(0)
    else:
        print(f"FAIL: unexpected data {got}")
        sys.exit(1)
