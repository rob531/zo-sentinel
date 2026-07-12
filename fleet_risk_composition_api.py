# deps: requests
"""Fleet risk composition API -- risk tier distribution across all servers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/fleet", tags=["fleet"])


class TierCount(BaseModel):
    tier: str
    count: int
    percentage: float


class RiskCompositionResponse(BaseModel):
    tiers: List[TierCount]
    total_servers: int
    last_updated: str


@router.get("/risk-composition", response_model=RiskCompositionResponse)
def get_risk_composition(db: Session = Depends(get_session)) -> RiskCompositionResponse:
    """Return risk tier distribution across all registered servers."""
    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0

    rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if total == 0:
        return RiskCompositionResponse(
            tiers=[],
            total_servers=0,
            last_updated=now
        )

    tiers = []
    for tier, count in rows:
        tier_label = tier if tier else "UNKNOWN"
        pct = round((count / total) * 100, 2) if total > 0 else 0.0
        tiers.append(TierCount(tier=tier_label, count=count, percentage=pct))

    return RiskCompositionResponse(
        tiers=tiers,
        total_servers=total,
        last_updated=now
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    # Seed 5 servers across 3 tiers: 2 HIGH, 2 MEDIUM, 1 LOW
    s.add(McpServerRegistry(server_id="srv1", name="Alpha", url="https://example.com/alpha", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv2", name="Beta", url="https://example.com/beta", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv3", name="Gamma", url="https://example.com/gamma", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv4", name="Delta", url="https://example.com/delta", risk_tier="MEDIUM"))
    s.add(McpServerRegistry(server_id="srv5", name="Epsilon", url="https://example.com/epsilon", risk_tier="LOW"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    r = c.get("/fleet/risk-composition")
    assert r.status_code == 200, r.text
    j = r.json()

    total = j["total_servers"]
    assert total == 5, f"Expected 5 total servers, got {total}"

    tier_counts = {t["tier"]: t["count"] for t in j["tiers"]}
    assert tier_counts.get("HIGH", 0) == 2, tier_counts
    assert tier_counts.get("MEDIUM", 0) == 2, tier_counts
    assert tier_counts.get("LOW", 0) == 1, tier_counts

    pct_sum = sum(t["percentage"] for t in j["tiers"])
    assert 99.9 <= pct_sum <= 100.1, f"Expected percentages to sum to ~100, got {pct_sum}"

    assert "last_updated" in j

    print("PASS")
