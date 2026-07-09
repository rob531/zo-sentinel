"""org_health_dashboard_api.py -- org-level risk summary endpoint.

GET /orgs/{org_id}/dashboard  ->  org metadata + server tier distribution +
avg p_top per axis, aggregated across all servers the org has touched.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    Org, User, ApiKey, McpServerRegistry, McpLlmAxisScore,
)

router = APIRouter(prefix="/orgs", tags=["org-health"])

TIERS = (
    "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT",
)
AXES = (
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface",
)


class OrgDashboard(BaseModel):
    org_id: str
    name: str
    user_count: int
    api_key_count: int
    server_count: int
    tier_distribution: Dict[str, int]
    axis_summary: Dict[str, Optional[float]]
    last_activity: Optional[str]


@router.get("/{org_id}/dashboard", response_model=OrgDashboard)
def get_org_dashboard(org_id: str, db: Session = Depends(get_session)) -> OrgDashboard:
    """Org-level risk summary: user/key counts, server tier distribution, avg p_top per axis."""
    org = db.get(Org, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail=f"Org {org_id!r} not found")

    # Counts from auth tables
    user_count = db.execute(
        select(func.count()).select_from(User).where(User.org_id == org_id)
    ).scalar() or 0
    api_key_count = db.execute(
        select(func.count()).select_from(ApiKey).where(ApiKey.org_id == org_id)
    ).scalar() or 0

    # All servers that have been scored (have axis rows) + their risk_tier from registry
    # The org's "servers" = unique server_ids that appear in the axis scores table
    scored_sub = select(McpLlmAxisScore.server_id).distinct()
    servers = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
        .where(McpServerRegistry.server_id.in_(scored_sub))
    ).all()
    server_count = len(servers)

    # Tier distribution
    tier_dist: Dict[str, int] = {t: 0 for t in TIERS}
    for sid, tier in servers:
        if tier and tier in tier_dist:
            tier_dist[tier] += 1
        elif tier:
            tier_dist.setdefault(tier, 0)
            tier_dist[tier] += 1

    # Avg p_top per axis across all scored servers
    axis_summary: Dict[str, Optional[float]] = {}
    for ax in AXES:
        avg = db.execute(
            select(func.avg(McpLlmAxisScore.p_top))
            .where(McpLlmAxisScore.axis_name == ax)
        ).scalar()
        axis_summary[ax] = round(float(avg), 4) if avg is not None else None

    # Last activity: most recent scored_at across all axis rows
    last_ts = db.execute(
        select(func.max(McpLlmAxisScore.scored_at))
    ).scalar()
    last_activity: Optional[str] = None
    if last_ts:
        last_activity = last_ts.isoformat() if isinstance(last_ts, datetime) else str(last_ts)

    return OrgDashboard(
        org_id=org_id,
        name=org.name,
        user_count=user_count,
        api_key_count=api_key_count,
        server_count=server_count,
        tier_distribution=tier_dist,
        axis_summary=axis_summary,
        last_activity=last_activity,
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

    # Seed org
    s.add(Org(id="test-org", name="Test Org"))
    # Seed user + api_key for the org
    s.add(User(id="u1", email="u@test.org", password_hash="xxx", org_id="test-org"))
    s.add(ApiKey(id="k1", org_id="test-org", key_hash="xxx"))

    # Seed 7 servers, one per tier
    for i, tier in enumerate((
        "TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT", "INSUFFICIENT",
    )):
        sid = f"srv{i+1}"
        s.add(McpServerRegistry(server_id=sid, name=f"Server {i+1}", risk_tier=tier))
        for j, ax in enumerate(AXES):
            s.add(McpLlmAxisScore(
                id=i * 10 + j + 1,
                server_id=sid,
                axis_name=ax,
                label="MEDIUM",
                model_version="v3.0_40974559",
            ))
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

    r = c.get("/orgs/test-org/dashboard")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["org_id"] == "test-org", j
    assert j["name"] == "Test Org", j
    assert j["user_count"] == 1, j
    assert j["api_key_count"] == 1, j
    assert j["server_count"] == 7, j
    td = j["tier_distribution"]
    assert set(TIERS) == set(td.keys()), f"Expected all 7 tiers, got {list(td.keys())}"
    for tier in TIERS:
        assert tier in td, f"Missing tier {tier}"
    assert td["TRUSTED_GENERAL"] == 1, td
    assert td["KNOWN_THREAT"] == 1, td
    assert j["axis_summary"].keys() == set(AXES), j["axis_summary"]
    assert isinstance(j["last_activity"], str), j

    # 404 for unknown org
    assert c.get("/orgs/nope/dashboard").status_code == 404

    print("PASS")