# deps: fastapi, sqlalchemy, pydantic
"""MCP Risk Tier Distribution API -- distribution of servers across risk tiers.

GET /api/mcp_risk_tier_distribution/
  Returns server counts and percentages per risk tier, overall and broken
  down by registry_source.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_risk_tier_distribution_api"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class TierCount(BaseModel):
    tier: str
    count: int
    pct: float = Field(..., ge=0, le=100)


class SourceBreakdown(BaseModel):
    source: str
    tiers: List[TierCount]
    total: int


class RiskTierDistributionResponse(BaseModel):
    generated_at: str
    total_servers: int
    overall: List[TierCount]
    by_source: List[SourceBreakdown]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_ALL_TIERS = [
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "UNKNOWN",
]


def _build_tier_counts(tier_counts: Dict[str, int], total: int) -> List[TierCount]:
    if total == 0:
        return []
    result = []
    for tier in _ALL_TIERS:
        cnt = tier_counts.get(tier, 0)
        if cnt:
            result.append(TierCount(tier=tier, count=cnt, pct=round((cnt / total) * 100, 2)))
    # include any tier not in _ALL_TIERS
    for tier, cnt in tier_counts.items():
        if tier not in _ALL_TIERS and cnt:
            result.append(TierCount(tier=tier, count=cnt, pct=round((cnt / total) * 100, 2)))
    return result


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/mcp_risk_tier_distribution/",
    response_model=RiskTierDistributionResponse,
    name="risk_tier_distribution:overview",
)
def get_risk_tier_distribution(
    db: Session = Depends(get_session),
) -> RiskTierDistributionResponse:
    """
    Return the distribution of MCP servers across risk tiers.

    - `overall`: count and percentage per tier across all servers.
    - `by_source`: the same breakdown scoped to each registry_source.
    """
    now = datetime.now(timezone.utc).isoformat()

    # --- overall ---
    total_servers = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar() or 0

    overall_rows = (
        db.execute(
            select(
                McpServerRegistry.risk_tier,
                func.count(McpServerRegistry.server_id).label("cnt"),
            )
            .group_by(McpServerRegistry.risk_tier)
        )
        .all()
    )
    overall_counts: Dict[str, int] = {row.risk_tier or "UNKNOWN": row.cnt for row in overall_rows}
    overall = _build_tier_counts(overall_counts, total_servers)

    # --- by source ---
    source_rows = (
        db.execute(
            select(
                McpServerRegistry.registry_source,
                McpServerRegistry.risk_tier,
                func.count(McpServerRegistry.server_id).label("cnt"),
            )
            .group_by(McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
        )
        .all()
    )

    # Build per-source tier map
    source_map: Dict[str, Dict[str, int]] = {}
    source_totals: Dict[str, int] = {}
    for row in source_rows:
        src = row.registry_source or "unknown"
        tier = row.risk_tier or "UNKNOWN"
        if src not in source_map:
            source_map[src] = {}
        source_map[src][tier] = row.cnt
        source_totals[src] = source_totals.get(src, 0) + row.cnt

    by_source: List[SourceBreakdown] = []
    for src in sorted(source_map.keys()):
        tiers = _build_tier_counts(source_map[src], source_totals[src])
        by_source.append(SourceBreakdown(source=src, tiers=tiers, total=source_totals[src]))

    return RiskTierDistributionResponse(
        generated_at=now,
        total_servers=total_servers,
        overall=overall,
        by_source=by_source,
    )


@router.get(
    "/mcp_risk_tier_distribution/summary",
    response_model=List[TierCount],
    name="risk_tier_distribution:summary",
)
def get_risk_tier_summary(
    db: Session = Depends(get_session),
    source: Optional[str] = Query(None, description="Filter by registry_source"),
) -> List[TierCount]:
    """Return simple tier counts, optionally filtered to a specific registry_source."""
    total = db.execute(
        select(func.count(McpServerRegistry.server_id)).where(
            McpServerRegistry.registry_source == source if source else True
        )
    ).scalar() or 0

    if total == 0:
        return []

    query = (
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .group_by(McpServerRegistry.risk_tier)
    )
    if source:
        query = query.where(McpServerRegistry.registry_source == source)

    rows = db.execute(query).all()
    counts = {row.risk_tier or "UNKNOWN": row.cnt for row in rows}
    return _build_tier_counts(counts, total)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base, McpServerRegistry
    from app.db import get_session as _real_get_session

    # Isolated FastAPI app
    _app = FastAPI()
    _app.include_router(router)

    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(_engine)
    _TestSession = sessionmaker(bind=_engine)
    _test_db = _TestSession()

    # Seed test data
    _servers = [
        McpServerRegistry(server_id="s1", name="S1", registry_source="npm", risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(server_id="s2", name="S2", registry_source="npm", risk_tier="TRUSTED_GENERAL"),
        McpServerRegistry(server_id="s3", name="S3", registry_source="npm", risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id="s4", name="S4", registry_source="github", risk_tier="ENTERPRISE_CONTROLLED"),
        McpServerRegistry(server_id="s5", name="S5", registry_source="github", risk_tier="CAUTION_LIMITED"),
        McpServerRegistry(server_id="s6", name="S6", registry_source="github", risk_tier=None),  # UNKNOWN
    ]
    for s in _servers:
        _test_db.add(s)
    _test_db.commit()

    def _override():
        try:
            yield _test_db
        finally:
            pass

    _app.dependency_overrides[_real_get_session] = _override
    _client = TestClient(_app)

    # --- Happy path: overview ---
    _resp = _client.get("/api/mcp_risk_tier_distribution/")
    if _resp.status_code != 200:
        print(f"FAIL: status {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    for _key in ("generated_at", "total_servers", "overall", "by_source"):
        if _key not in _data:
            print(f"FAIL: missing key '{_key}' in response: {_data}")
            _sys.exit(1)
    if _data["total_servers"] != 6:
        print(f"FAIL: expected total_servers=6, got {_data['total_servers']}")
        _sys.exit(1)
    # Check overall tiers present
    _tier_names = {t["tier"] for t in _data["overall"]}
    for _expected in ("TRUSTED_GENERAL", "HIGH_RISK_ISOLATED", "ENTERPRISE_CONTROLLED", "CAUTION_LIMITED", "UNKNOWN"):
        if _expected not in _tier_names:
            print(f"FAIL: expected tier '{_expected}' in overall: {_tier_names}")
            _sys.exit(1)

    # --- by_source npm (3 servers) ---
    _npm = next((b for b in _data["by_source"] if b["source"] == "npm"), None)
    if not _npm:
        print("FAIL: no 'npm' source in by_source")
        _sys.exit(1)
    if _npm["total"] != 3:
        print(f"FAIL: expected npm total=3, got {_npm['total']}")
        _sys.exit(1)

    # --- by_source github (3 servers) ---
    _gh = next((b for b in _data["by_source"] if b["source"] == "github"), None)
    if not _gh:
        print("FAIL: no 'github' source in by_source")
        _sys.exit(1)
    if _gh["total"] != 3:
        print(f"FAIL: expected github total=3, got {_gh['total']}")
        _sys.exit(1)

    # --- Summary endpoint ---
    _resp2 = _client.get("/api/mcp_risk_tier_distribution/summary")
    if _resp2.status_code != 200:
        print(f"FAIL: summary endpoint status {_resp2.status_code}: {_resp2.text}")
        _sys.exit(1)
    _sum = _resp2.json()
    if not isinstance(_sum, list) or len(_sum) == 0:
        print(f"FAIL: expected non-empty list from summary, got {_sum}")
        _sys.exit(1)

    # --- Summary with source filter ---
    _resp3 = _client.get("/api/mcp_risk_tier_distribution/summary?source=npm")
    if _resp3.status_code != 200:
        print(f"FAIL: summary?source=npm status {_resp3.status_code}: {_resp3.text}")
        _sys.exit(1)
    _sum_npm = _resp3.json()
    _npm_tiers_total = sum(t["count"] for t in _sum_npm)
    if _npm_tiers_total != 3:
        print(f"FAIL: expected npm summary total=3, got {_npm_tiers_total}")
        _sys.exit(1)

    # --- Auth/permission failure: clear override ---
    _app.dependency_overrides.clear()
    _resp4 = _client.get("/api/mcp_risk_tier_distribution/")
    if _resp4.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {_resp4.status_code}")
        _sys.exit(1)

    print("PASS")
