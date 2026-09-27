# deps: fastapi, pydantic, sqlalchemy
"""Risk Tier Distribution Snapshot API.

Returns a point-in-time snapshot of the risk tier landscape:
  - overall tier counts and percentages across all servers
  - per-source breakdown of tier counts
  - full paginated server-to-tier mapping with server metadata

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: mcp_server_registry via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk_tier_distribution_snapshot"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class TierCount(BaseModel):
    tier: str
    count: int
    pct: float


class SourceBreakdown(BaseModel):
    source: str
    tiers: List[TierCount]
    total: int


class ServerSnapshotEntry(BaseModel):
    server_id: str
    name: str | None
    url: str | None
    registry_source: str | None
    risk_tier: str | None
    confidence: float | None


class RiskTierDistributionSnapshotResponse(BaseModel):
    snapshot_at: str
    total_servers: int
    overall: List[TierCount]
    by_source: List[SourceBreakdown]
    servers: List[ServerSnapshotEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_CANONICAL_TIERS = [
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
    for tier in _CANONICAL_TIERS:
        cnt = tier_counts.get(tier, 0)
        if cnt:
            result.append(TierCount(tier=tier, count=cnt, pct=round((cnt / total) * 100, 2)))
    for tier, cnt in tier_counts.items():
        if tier not in _CANONICAL_TIERS and cnt:
            result.append(TierCount(tier=tier, count=cnt, pct=round((cnt / total) * 100, 2)))
    return result


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk_tier_distribution_snapshot",
    response_model=RiskTierDistributionSnapshotResponse,
    name="risk_tier_distribution_snapshot:full",
)
def get_risk_tier_distribution_snapshot(
    limit: int = Query(default=200, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> RiskTierDistributionSnapshotResponse:
    """
    Return a full point-in-time snapshot: overall tier distribution,
    by-source breakdown, and paginated server-to-tier entries.
    """
    now = datetime.now(timezone.utc).isoformat()

    # --- overall counts ---
    total_servers = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar() or 0

    overall_rows = (
        db.execute(
            select(
                McpServerRegistry.risk_tier,
                func.count(McpServerRegistry.server_id).label("cnt"),
            ).group_by(McpServerRegistry.risk_tier)
        )
        .all()
    )
    overall_counts: Dict[str, int] = {row.risk_tier or "UNKNOWN": row.cnt for row in overall_rows}
    overall = _build_tier_counts(overall_counts, total_servers)

    # --- by-source breakdown ---
    source_rows = (
        db.execute(
            select(
                McpServerRegistry.registry_source,
                McpServerRegistry.risk_tier,
                func.count(McpServerRegistry.server_id).label("cnt"),
            ).group_by(McpServerRegistry.registry_source, McpServerRegistry.risk_tier)
        )
        .all()
    )

    source_map: Dict[str, Dict[str, int]] = {}
    source_totals: Dict[str, int] = {}
    for row in source_rows:
        src = row.registry_source or "unknown"
        tier = row.risk_tier or "UNKNOWN"
        if src not in source_map:
            source_map[src] = {}
        source_map[src][tier] = row.cnt
        source_totals[src] = source_totals.get(src, 0) + row.cnt

    by_source: List[SourceBreakdown] = [
        SourceBreakdown(
            source=src,
            tiers=_build_tier_counts(source_map[src], source_totals[src]),
            total=source_totals[src],
        )
        for src in sorted(source_map)
    ]

    # --- paginated server entries ---
    all_server_rows = (
        db.execute(
            select(McpServerRegistry).order_by(McpServerRegistry.server_id)
        )
        .scalars()
        .all()
    )
    total_count = len(all_server_rows)
    paginated_rows = all_server_rows[offset : offset + limit]

    servers = [
        ServerSnapshotEntry(
            server_id=s.server_id,
            name=s.name,
            url=s.url,
            registry_source=s.registry_source,
            risk_tier=s.risk_tier,
            confidence=s.confidence,
        )
        for s in paginated_rows
    ]

    return RiskTierDistributionSnapshotResponse(
        snapshot_at=now,
        total_servers=total_count,
        overall=overall,
        by_source=by_source,
        servers=servers,
    )


@router.get(
    "/risk_tier_distribution_snapshot/summary",
    response_model=List[TierCount],
    name="risk_tier_distribution_snapshot:summary",
)
def get_risk_tier_distribution_snapshot_summary(
    source: str | None = Query(None, description="Filter by registry_source"),
    db: Session = Depends(get_session),
) -> List[TierCount]:
    """
    Return overall tier counts and percentages, optionally scoped to a
    specific registry_source.
    """
    total = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar() or 0

    if total == 0:
        return []

    query = (
        select(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        ).group_by(McpServerRegistry.risk_tier)
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

    from app.db import get_session as _real_get_session
    from app.models import Base, McpServerRegistry

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

    _test_db.add_all([
        McpServerRegistry(server_id="s1", name="S1", registry_source="npm", risk_tier="TRUSTED_GENERAL", confidence=0.95),
        McpServerRegistry(server_id="s2", name="S2", registry_source="npm", risk_tier="TRUSTED_GENERAL", confidence=0.90),
        McpServerRegistry(server_id="s3", name="S3", registry_source="npm", risk_tier="HIGH_RISK_ISOLATED", confidence=0.30),
        McpServerRegistry(server_id="s4", name="S4", registry_source="github", risk_tier="ENTERPRISE_CONTROLLED", confidence=0.85),
        McpServerRegistry(server_id="s5", name="S5", registry_source="github", risk_tier="CAUTION_LIMITED", confidence=0.50),
        McpServerRegistry(server_id="s6", name="S6", registry_source="github", risk_tier=None, confidence=None),
    ])
    _test_db.commit()

    def _override():
        try:
            yield _test_db
        finally:
            pass

    _app.dependency_overrides[_real_get_session] = _override
    _client = TestClient(_app)

    # --- Full snapshot ---
    _resp = _client.get("/api/risk_tier_distribution_snapshot")
    if _resp.status_code != 200:
        print(f"FAIL: status {_resp.status_code}: {_resp.text}")
        _sys.exit(1)
    _data = _resp.json()
    for _key in ("snapshot_at", "total_servers", "overall", "by_source", "servers"):
        if _key not in _data:
            print(f"FAIL: missing key '{_key}' in response: {_data}")
            _sys.exit(1)
    if _data["total_servers"] != 6:
        print(f"FAIL: expected total_servers=6, got {_data['total_servers']}")
        _sys.exit(1)
    _tier_names = {t["tier"] for t in _data["overall"]}
    for _exp in ("TRUSTED_GENERAL", "HIGH_RISK_ISOLATED", "ENTERPRISE_CONTROLLED", "CAUTION_LIMITED", "UNKNOWN"):
        if _exp not in _tier_names:
            print(f"FAIL: expected tier '{_exp}' in overall: {_tier_names}")
            _sys.exit(1)

    # --- by_source npm ---
    _npm = next((b for b in _data["by_source"] if b["source"] == "npm"), None)
    if not _npm:
        print("FAIL: no 'npm' source in by_source")
        _sys.exit(1)
    if _npm["total"] != 3:
        print(f"FAIL: expected npm total=3, got {_npm['total']}")
        _sys.exit(1)

    # --- by_source github ---
    _gh = next((b for b in _data["by_source"] if b["source"] == "github"), None)
    if not _gh:
        print("FAIL: no 'github' source in by_source")
        _sys.exit(1)
    if _gh["total"] != 3:
        print(f"FAIL: expected github total=3, got {_gh['total']}")
        _sys.exit(1)

    # --- Pagination ---
    _resp2 = _client.get("/api/risk_tier_distribution_snapshot?limit=3&offset=0")
    if _resp2.status_code != 200:
        print(f"FAIL: paginated snapshot status {_resp2.status_code}: {_resp2.text}")
        _sys.exit(1)
    _pag = _resp2.json()
    if len(_pag["servers"]) != 3:
        print(f"FAIL: expected 3 paginated servers, got {len(_pag['servers'])}")
        _sys.exit(1)
    if _pag["total_servers"] != 6:
        print(f"FAIL: expected total_servers=6 in paginated response, got {_pag['total_servers']}")
        _sys.exit(1)

    # --- Summary endpoint ---
    _resp3 = _client.get("/api/risk_tier_distribution_snapshot/summary")
    if _resp3.status_code != 200:
        print(f"FAIL: summary status {_resp3.status_code}: {_resp3.text}")
        _sys.exit(1)
    _sum = _resp3.json()
    if not isinstance(_sum, list) or len(_sum) == 0:
        print(f"FAIL: expected non-empty list from summary, got {_sum}")
        _sys.exit(1)
    _sum_total = sum(t["count"] for t in _sum)
    if _sum_total != 6:
        print(f"FAIL: expected summary total=6, got {_sum_total}")
        _sys.exit(1)

    # --- Summary with source filter ---
    _resp4 = _client.get("/api/risk_tier_distribution_snapshot/summary?source=npm")
    if _resp4.status_code != 200:
        print(f"FAIL: summary?source=npm status {_resp4.status_code}: {_resp4.text}")
        _sys.exit(1)
    _sum_npm = _resp4.json()
    _npm_total = sum(t["count"] for t in _sum_npm)
    if _npm_total != 3:
        print(f"FAIL: expected npm summary total=3, got {_npm_total}")
        _sys.exit(1)

    # --- Auth failure without session override ---
    _app.dependency_overrides.clear()
    _resp5 = _client.get("/api/risk_tier_distribution_snapshot")
    if _resp5.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {_resp5.status_code}")
        _sys.exit(1)

    print("PASS")
