from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class TierSummary(BaseModel):
    tier: str
    count: int
    pct: float


class TierSummaryResponse(BaseModel):
    tiers: list[TierSummary]


def get_tier_counts(
    session: Session = Depends(get_session),
    org_id: str | None = Header(None, alias="X-Org-Id"),
) -> TierSummaryResponse:
    """
    Count servers per risk_tier.
    """
    stmt = select(
        McpServerRegistry.risk_tier,
        func.count(McpServerRegistry.server_id).label("count")
    ).group_by(McpServerRegistry.risk_tier)
    
    results = session.execute(stmt).all()
    
    total = sum(r.count for r in results)
    tiers = [
        TierSummary(
            tier=r.risk_tier,
            count=r.count,
            pct=round((r.count / total) * 100, 2) if total else 0.0
        )
        for r in results
    ]
    
    return TierSummaryResponse(tiers=tiers)


@router.get("/api/risk/tier-summary", response_model=TierSummaryResponse)
def get_tier_summary(
    session: Session = Depends(get_session),
    org_id: str | None = Header(None, alias="X-Org-Id"),
) -> TierSummaryResponse:
    return get_tier_counts(session=session, org_id=org_id)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    db = TestingSessionLocal()
    db.add(McpServerRegistry(server_id="s1", risk_tier="TRUSTED_GENERAL"))
    db.add(McpServerRegistry(server_id="s2", risk_tier="TRUSTED_GENERAL"))
    db.add(McpServerRegistry(server_id="s3", risk_tier="ENTERPRISE_CONTROLLED"))
    db.add(McpServerRegistry(server_id="s4", risk_tier="ENTERPRISE_CONTROLLED"))
    db.add(McpServerRegistry(server_id="s5", risk_tier="HIGH_RISK_ISOLATED"))
    db.add(McpServerRegistry(server_id="s6", risk_tier="HIGH_RISK_ISOLATED"))
    db.commit()
    db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    client = TestClient(that_app)
    response = client.get("/api/risk/tier-summary")
    assert response.status_code == 200
    data = response.json()
    total = sum(t["count"] for t in data["tiers"])
    assert total == 6, f"expected 6, got {total}"
    assert len(data["tiers"]) >= 1
    print("PASS")