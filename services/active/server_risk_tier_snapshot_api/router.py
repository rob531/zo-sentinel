# deps: fastapi, pydantic, sqlalchemy
"""Server Risk Tier Snapshot API.

Provides a point-in-time snapshot of the risk tier landscape -- current distribution
of servers across tiers and the full server-to-tier mapping as of the requested
moment (defaults to now).

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Ensure repo root is on the path so `app` package resolves in all contexts
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_tier_snapshot_api"])


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #

class TierCounts(BaseModel):
    tier: str
    count: int
    pct: float


class RiskTierSnapshotSummary(BaseModel):
    snapshot_at: datetime
    total_servers: int
    scored_servers: int
    unscored_servers: int
    tier_distribution: List[TierCounts]


class ServerSnapshotEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    label: Optional[str]
    model_version: Optional[str]
    scored_at: Optional[datetime]
    confidence: Optional[float]


class RiskTierSnapshotFull(BaseModel):
    snapshot_at: datetime
    summary: RiskTierSnapshotSummary
    servers: List[ServerSnapshotEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_TIER_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED", "UNKNOWN"]


def _tier_label(label: Optional[str]) -> str:
    if label is None:
        return "UNKNOWN"
    l = label.upper()
    if l in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED"):
        return l
    return "UNKNOWN"


def _compute_summary(
    db: Session,
    snapshot_at: datetime,
) -> RiskTierSnapshotSummary:
    cutoff = snapshot_at + timedelta(seconds=0)

    subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at <= cutoff,
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    latest_q = (
        select(McpLlmAxisScore)
        .join(
            subq,
            and_(
                McpLlmAxisScore.server_id == subq.c.server_id,
                McpLlmAxisScore.scored_at == subq.c.max_scored_at,
                McpLlmAxisScore.axis_name == "overall_risk",
            ),
        )
    )
    latest_rows: List[McpLlmAxisScore] = list(db.execute(latest_q).scalars().all())

    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    scored_servers = len(latest_rows)
    unscored_servers = total_servers - scored_servers

    tier_counts: Dict[str, int] = {}
    for row in latest_rows:
        tier = _tier_label(row.label)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    for t in _TIER_ORDER:
        if t not in tier_counts:
            tier_counts[t] = 0

    total_labeled = scored_servers
    distribution = [
        TierCounts(
            tier=t,
            count=tier_counts.get(t, 0),
            pct=round(tier_counts.get(t, 0) / total_labeled * 100, 2)
            if total_labeled > 0
            else 0.0,
        )
        for t in _TIER_ORDER
    ]

    return RiskTierSnapshotSummary(
        snapshot_at=snapshot_at,
        total_servers=total_servers,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        tier_distribution=distribution,
    )


def _fetch_server_entries(
    db: Session,
    snapshot_at: datetime,
) -> List[ServerSnapshotEntry]:
    cutoff = snapshot_at + timedelta(seconds=0)

    subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.scored_at <= cutoff)
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .subquery()
    )

    latest_q = (
        select(McpLlmAxisScore)
        .join(
            subq,
            and_(
                McpLlmAxisScore.server_id == subq.c.server_id,
                McpLlmAxisScore.scored_at == subq.c.max_scored_at,
            ),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
    )
    score_map: Dict[str, McpLlmAxisScore] = {}
    for row in db.execute(latest_q).scalars().all():
        score_map[row.server_id] = row

    servers = db.query(McpServerRegistry).all()

    entries: List[ServerSnapshotEntry] = []
    for srv in servers:
        score = score_map.get(srv.server_id)
        entries.append(
            ServerSnapshotEntry(
                server_id=srv.server_id,
                name=srv.name,
                url=srv.url,
                registry_source=srv.registry_source,
                risk_tier=_tier_label(score.label) if score else srv.risk_tier,
                p_top=score.p_top if score else None,
                p_critical=score.p_critical if score else None,
                p_danger=score.p_danger if score else None,
                label=score.label if score else None,
                model_version=score.model_version if score else None,
                scored_at=score.scored_at if score else None,
                confidence=srv.confidence,
            )
        )
    return entries


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/risk-tier-snapshot/summary",
    response_model=RiskTierSnapshotSummary,
)
def get_snapshot_summary(
    at: Optional[datetime] = Query(
        default=None,
        description="Point-in-time for snapshot (UTC). Defaults to now.",
    ),
    db: Session = Depends(get_session),
) -> RiskTierSnapshotSummary:
    """
    Return a summary of the risk tier landscape at the requested moment:
    total / scored / unscored servers and the tier distribution percentages.
    """
    snapshot_at = at if at is not None else datetime.now(timezone.utc)
    return _compute_summary(db, snapshot_at)


@router.get(
    "/risk-tier-snapshot",
    response_model=RiskTierSnapshotFull,
)
def get_full_snapshot(
    at: Optional[datetime] = Query(
        default=None,
        description="Point-in-time for snapshot (UTC). Defaults to now.",
    ),
    limit: int = Query(default=1000, ge=1, le=50000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
) -> RiskTierSnapshotFull:
    """
    Return a full point-in-time snapshot: summary + paginated server-level
    risk tier entries.
    """
    snapshot_at = at if at is not None else datetime.now(timezone.utc)
    summary = _compute_summary(db, snapshot_at)
    all_entries = _fetch_server_entries(db, snapshot_at)
    paginated = all_entries[offset : offset + limit]
    return RiskTierSnapshotFull(
        snapshot_at=snapshot_at,
        summary=summary,
        servers=paginated,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Ensure repo root is on the path so `app` package resolves
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    from app.db import Base
    from app.models import McpServerRegistry, McpLlmAxisScore

    Base.metadata.create_all(test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    two_days_ago = now - timedelta(days=2)

    with TestSessionLocal() as sess:
        sess.add_all([
            McpServerRegistry(server_id="srv-1", name="Server One", risk_tier="HIGH", confidence=0.9),
            McpServerRegistry(server_id="srv-2", name="Server Two", risk_tier="MEDIUM", confidence=0.7),
            McpServerRegistry(server_id="srv-3", name="Server Three", risk_tier="LOW", confidence=0.6),
            McpServerRegistry(server_id="srv-4", name="Server Four", risk_tier=None, confidence=None),
        ])
        sess.commit()

        sess.add_all([
            McpLlmAxisScore(
                id=1,
                server_id="srv-1", axis_name="overall_risk", label="CRITICAL",
                p_top=0.88, p_critical=0.5, p_danger=0.3,
                model_version="v0", scored_at=two_days_ago,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-1", axis_name="overall_risk", label="HIGH",
                p_top=0.68, p_critical=0.3, p_danger=0.2,
                model_version="v1", scored_at=yesterday,
            ),
            McpLlmAxisScore(
                id=3,
                server_id="srv-2", axis_name="overall_risk", label="MEDIUM",
                p_top=0.50, p_critical=0.1, p_danger=0.1,
                model_version="v1", scored_at=yesterday,
            ),
            McpLlmAxisScore(
                id=4,
                server_id="srv-3", axis_name="overall_risk", label="LOW",
                p_top=0.20, p_critical=0.05, p_danger=0.02,
                model_version="v1", scored_at=yesterday,
            ),
        ])
        sess.commit()

    client = TestClient(test_app)

    resp1 = client.get("/api/risk-tier-snapshot/summary")
    if resp1.status_code != 200:
        print(f"FAIL: summary returned {resp1.status_code}: {resp1.text}")
        sys.exit(1)
    summary = resp1.json()
    if summary["total_servers"] != 4:
        print(f"FAIL: expected 4 total_servers, got {summary['total_servers']}")
        sys.exit(1)
    if summary["scored_servers"] != 3:
        print(f"FAIL: expected 3 scored_servers, got {summary['scored_servers']}")
        sys.exit(1)
    if summary["unscored_servers"] != 1:
        print(f"FAIL: expected 1 unscored_servers, got {summary['unscored_servers']}")
        sys.exit(1)

    resp2 = client.get("/api/risk-tier-snapshot")
    if resp2.status_code != 200:
        print(f"FAIL: full snapshot returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    full = resp2.json()
    if len(full["servers"]) != 4:
        print(f"FAIL: expected 4 server entries, got {len(full['servers'])}")
        sys.exit(1)

    tier_map = {s["server_id"]: s["risk_tier"] for s in full["servers"]}
    if tier_map.get("srv-1") != "HIGH":
        print(f"FAIL: expected srv-1 risk_tier HIGH (latest score), got {tier_map.get('srv-1')}")
        sys.exit(1)
    if tier_map.get("srv-2") != "MEDIUM":
        print(f"FAIL: expected srv-2 risk_tier MEDIUM, got {tier_map.get('srv-2')}")
        sys.exit(1)
    if tier_map.get("srv-3") != "LOW":
        print(f"FAIL: expected srv-3 risk_tier LOW, got {tier_map.get('srv-3')}")
        sys.exit(1)
    if tier_map.get("srv-4") != "UNKNOWN":
        print(f"FAIL: expected srv-4 risk_tier UNKNOWN (no score), got {tier_map.get('srv-4')}")
        sys.exit(1)

    resp3 = client.get(f"/api/risk-tier-snapshot?at={two_days_ago.isoformat()}")
    if resp3.status_code != 200:
        print(f"FAIL: past snapshot returned {resp3.status_code}")
        sys.exit(1)
    past = resp3.json()
    past_tier_map = {s["server_id"]: s["risk_tier"] for s in past["servers"]}
    if past_tier_map.get("srv-1") != "CRITICAL":
        print(f"FAIL: expected srv-1 CRITICAL at past time, got {past_tier_map.get('srv-1')}")
        sys.exit(1)

    resp4 = client.get("/api/risk-tier-snapshot?limit=2&offset=0")
    if resp4.status_code != 200:
        print(f"FAIL: paginated snapshot returned {resp4.status_code}")
        sys.exit(1)
    paginated = resp4.json()
    if len(paginated["servers"]) != 2:
        print(f"FAIL: expected 2 paginated servers, got {len(paginated['servers'])}")
        sys.exit(1)

    print("PASS")
