"""risk_tier_summary_api.py -- global risk-tier distribution across all MCP servers.

Returns a count of servers per risk_tier (from mcp_server_registry.risk_tier) plus
the total server count. Read-only; no auth required for aggregate stats.
"""
from __future__ import annotations

from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk"])


class RiskTierSummary(BaseModel):
    tiers: Dict[str, int]
    total_servers: int


@router.get("/risk_tier_summary", response_model=RiskTierSummary)
def get_risk_tier_summary(db=Depends(get_session)) -> RiskTierSummary:
    """Aggregate risk_tier counts across the full registry."""
    # Total servers (all rows, regardless of whether they have a tier label)
    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0

    # Count per tier (NULL tiers group under None, which serialises as "None" key)
    rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
    ).all()

    tiers: Dict[str, int] = {}
    for tier_val, cnt in rows:
        key = tier_val if tier_val is not None else "UNKNOWN"
        tiers[key] = cnt

    return RiskTierSummary(tiers=tiers, total_servers=total)


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

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    # Seed three servers spanning the required tiers
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Trusted General",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="TRUSTED_GENERAL"))
    s.add(McpServerRegistry(server_id="srv2", name="High Risk Isolated",
                            url="https://example.com/high-risk",
                            risk_tier="HIGH_RISK_ISOLATED"))
    s.add(McpServerRegistry(server_id="srv3", name="Caution Limited",
                            url="https://example.com/caution",
                            risk_tier="CAUTION_LIMITED"))
    s.commit()
    s.close()

    c = TestClient(app)
    r = c.get("/api/risk_tier_summary")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_servers"] == 3, j
    assert j["tiers"].get("TRUSTED_GENERAL") == 1, j
    assert j["tiers"].get("HIGH_RISK_ISOLATED") == 1, j
    assert j["tiers"].get("CAUTION_LIMITED") == 1, j
    print("PASS")