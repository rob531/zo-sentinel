# services/staged/server_risk_tier_distribution_dashboard/contract.py
from fastapi import FastAPI, APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List

from sqlalchemy import select, func
from sqlalchemy.orm import Session

# Real data layer imports (must remain unchanged)
from app.db import get_session, Base
from app.models import McpServerRegistry  # type: ignore

router = APIRouter(prefix="/api")


class TierInfo(BaseModel):
    tier: str
    count: int
    percentage: float


class RiskTierDistributionResponse(BaseModel):
    tiers: List[TierInfo]


@router.get(
    "/risk/tier/distribution",
    response_model=RiskTierDistributionResponse,
    name="get_risk_tier_distribution",
)
def get_risk_tier_distribution(session: Session = Depends(get_session)):
    stmt = (
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .order_by(McpServerRegistry.risk_tier)
    )
    rows = session.execute(stmt).all()
    total = sum(cnt for _, cnt in rows) or 1
    tiers = [
        TierInfo(
            tier=tier,
            count=cnt,
            percentage=round(cnt / total * 100, 2),
        )
        for tier, cnt in rows
    ]
    return RiskTierDistributionResponse(tiers=tiers)


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.server_risk_tier_distribution_dashboard.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for the acceptance test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    TestSession = sessionmaker(bind=engine)

    # Override the dependency to use the test session
    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    app.dependency_overrides[get_session] = get_test_session

    # Seed minimal data
    with TestSession() as sess:
        sess.add_all(
            [
                McpServerRegistry(server_id="s1", risk_tier="low"),
                McpServerRegistry(server_id="s2", risk_tier="low"),
                McpServerRegistry(server_id="s3", risk_tier="high"),
            ]
        )
        sess.commit()

    client = TestClient(app)
    resp = client.get("/api/risk/tier/distribution")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()
    assert "tiers" in data, "missing tiers key"
    tiers = data["tiers"]
    assert isinstance(tiers, list), "tiers not a list"
    assert len(tiers) == 2, f"expected 2 tiers, got {len(tiers)}"
    low_tier = next((t for t in tiers if t["tier"] == "low"), None)
    assert low_tier is not None, "low tier missing"
    assert low_tier["count"] == 2, f"expected count 2 for low tier, got {low_tier['count']}"
    print("PASS")