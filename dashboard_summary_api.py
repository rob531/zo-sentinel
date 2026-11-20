"""dashboard_summary_api.py -- GET /api/dashboard/summary (REAL implementation).

Replaces the hollow factory artifact (imported nonexistent app.database /
app.services -- it could never mount, which is why the SPA dashboard sat on
'// loading dashboard...' forever in prod; found in the 2026-07-02 treewalk).
Serves the shape app/static/app.html consumes:
  {scored, unassessed, registry_total, risk_distribution:{TIER:count}, by_source:[{source,count}]}
Published (post-trust-override) tiers straight from mcp_server_registry --
excludes 'unassessed' rows from `scored` and `risk_distribution`, and filters
internal pipeline/canary sources out of public `by_source`.
"""
from __future__ import annotations

import time
from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from clerk_m2m_auth import Principal, get_principal

router = APIRouter(prefix="/api", tags=["dashboard"])

_CACHE: dict = {"at": 0.0, "data": None}
CACHE_TTL_SECS = 120
_INTERNAL_SOURCES = ("candidate_promoter", "gate_canary", "test")


def compute_summary(db: Session) -> Dict:
    registry_total = db.execute(
        select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    scored = db.execute(
        select(func.count()).select_from(McpServerRegistry)
        .where(
            McpServerRegistry.risk_tier.is_not(None),
            func.lower(McpServerRegistry.risk_tier) != "unassessed",
        )).scalar() or 0
    dist_rows = db.execute(
        select(McpServerRegistry.risk_tier, func.count())
        .where(
            McpServerRegistry.risk_tier.is_not(None),
            func.lower(McpServerRegistry.risk_tier) != "unassessed",
        )
        .group_by(McpServerRegistry.risk_tier)).all()
    risk_distribution = {str(t).upper(): int(c) for t, c in dist_rows if t}
    src_rows = db.execute(
        select(McpServerRegistry.registry_source, func.count())
        .where(
            McpServerRegistry.registry_source.is_not(None),
            ~func.lower(McpServerRegistry.registry_source).in_(_INTERNAL_SOURCES),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(func.count().desc())).all()
    by_source: List[dict] = [{"source": str(s), "count": int(c)}
                             for s, c in src_rows[:10]]
    unassessed = max(0, int(registry_total) - int(scored))
    return {
        "scored": scored,
        "unassessed": unassessed,
        "registry_total": registry_total,
        "risk_distribution": risk_distribution,
        "by_source": by_source,
    }


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_session),
                      principal: Principal = Depends(get_principal)) -> dict:
    now = time.time()
    if _CACHE["data"] is None or now - _CACHE["at"] > CACHE_TTL_SECS:
        _CACHE["data"] = compute_summary(db)
        _CACHE["at"] = now
    return _CACHE["data"]


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry as R
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    s.add_all([
        R(server_id="a", risk_tier="HIGH", registry_source="github"),
        R(server_id="b", risk_tier="LOW", registry_source="npm"),
        R(server_id="c", risk_tier="unassessed", registry_source="github"),
        R(server_id="d", registry_source="gate_canary"),
    ])
    s.commit()
    d = compute_summary(s)
    assert d["registry_total"] == 4 and d["scored"] == 2 and d["unassessed"] == 2, d
    assert d["risk_distribution"] == {"HIGH": 1, "LOW": 1}, d
    assert {"source": "github", "count": 2} in d["by_source"], d
    assert all(x["source"] != "gate_canary" for x in d["by_source"]), d
    print("PASS")
