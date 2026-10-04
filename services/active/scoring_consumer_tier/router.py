# deps: fastapi, pydantic, sqlalchemy
"""Scoring Consumer Tier Router.

Reads SFT axis scores from `mcp_llm_axis_scores`, derives per-server risk tiers,
and exposes them for downstream consumers. Applies trust_gating_override so
official/verified publishers are not defamed as HIGH/CRITICAL.

Public endpoint (auth=public per directive).
Data: app Postgres via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import os
import sys

_repo_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

import sys as _sys
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["scoring_consumer_tier"])


# --------------------------------------------------------------------------- #
# Pydantic response shapes
# --------------------------------------------------------------------------- #

class TrustInfo(BaseModel):
    trusted: bool
    trust_basis: Optional[str] = None
    capped: bool
    masquerade_flag: bool
    display_label: str


class AxisScoreEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None


class ServerTierResponse(BaseModel):
    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    overall_risk_label: str
    published_risk_label: str
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    model_version: Optional[str] = None
    scored_at: Optional[datetime] = None
    axes: List[AxisScoreEntry]
    trust_info: TrustInfo


class TierDistributionItem(BaseModel):
    tier: str
    count: int
    pct: float


class TierSummaryResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    scored_servers: int
    unscored_servers: int
    tier_distribution: List[TierDistributionItem]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _derive_tier(
    p_top: Optional[float], p_critical: Optional[float]
) -> str:
    """Map SFT probability outputs to a qualitative risk tier label."""
    if p_top is None and p_critical is None:
        return "UNKNOWN"
    pc = p_critical or 0.0
    pt = p_top or 0.0
    if pc >= 0.5 or pt >= 0.8:
        return "CRITICAL"
    if pc >= 0.2 or pt >= 0.5:
        return "HIGH"
    if pc >= 0.05 or pt >= 0.2:
        return "MEDIUM"
    if pc >= 0.01 or pt >= 0.05:
        return "LOW"
    return "MINIMAL"


def _latest_overall_score(
    db: Session, server_id: str
) -> Optional[McpLlmAxisScore]:
    """Return the most recent overall_risk axis score for a server."""
    subq = (
        select(
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
        )
        .scalar_subquery()
    )
    row = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at == subq,
        )
        .first()
    )
    return row


def _all_axes(db: Session, server_id: str) -> List[McpLlmAxisScore]:
    """Return all axis score rows for a server (latest per axis)."""
    subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )
    rows = (
        db.query(McpLlmAxisScore)
        .join(
            subq,
            (
                (McpLlmAxisScore.axis_name == subq.c.axis_name)
                & (McpLlmAxisScore.scored_at == subq.c.max_scored_at)
            ),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    return rows


def _server_tier_response(
    srv: McpServerRegistry,
    overall_score: Optional[McpLlmAxisScore],
    all_axis_rows: List[McpLlmAxisScore],
) -> ServerTierResponse:
    """Build a ServerTierResponse, applying trust gating."""
    p_top = overall_score.p_top if overall_score else None
    p_critical = overall_score.p_critical if overall_score else None
    p_danger = overall_score.p_danger if overall_score else None
    model_version = overall_score.model_version if overall_score else None
    scored_at = overall_score.scored_at if overall_score else None

    derived = _derive_tier(p_top, p_critical)

    # Trust gate: cap the published verdict for verified publishers
    axis_labels = {
        row.axis_name: row.label for row in all_axis_rows if row.label
    }
    gate = trust_gate(srv.url, srv.name, axis_labels)

    trust_info = TrustInfo(
        trusted=gate["trusted"],
        trust_basis=gate["trust_basis"],
        capped=gate["capped"],
        masquerade_flag=gate["masquerade_flag"],
        display_label=gate["display_label"],
    )

    published_label = gate["published_overall_risk"]

    axes = [
        AxisScoreEntry(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
        )
        for row in all_axis_rows
    ]

    return ServerTierResponse(
        server_id=srv.server_id,
        name=srv.name,
        url=srv.url,
        registry_source=srv.registry_source,
        overall_risk_label=derived,
        published_risk_label=published_label,
        p_top=p_top,
        p_critical=p_critical,
        p_danger=p_danger,
        model_version=model_version,
        scored_at=scored_at,
        axes=axes,
        trust_info=trust_info,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring/consumer/tier/{server_id}",
    response_model=ServerTierResponse,
)
def get_server_tier(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerTierResponse:
    """
    Return the risk tier for a single server: all axis scores plus the
    derived and trust-gated overall risk labels.
    """
    srv = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    overall_score = _latest_overall_score(db, server_id)
    all_axis_rows = _all_axes(db, server_id)

    return _server_tier_response(srv, overall_score, all_axis_rows)


@router.get(
    "/scoring/consumer/tier",
    response_model=List[ServerTierResponse],
)
def list_server_tiers(
    limit: int = Query(default=100, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    risk_tier: Optional[str] = Query(
        default=None, description="Filter by published tier"
    ),
    db: Session = Depends(get_session),
) -> List[ServerTierResponse]:
    """
    Return a paginated list of servers with their current risk tiers
    and trust-gating metadata.
    """
    servers = (
        db.query(McpServerRegistry).offset(offset).limit(limit).all()
    )

    results: List[ServerTierResponse] = []
    for srv in servers:
        overall_score = _latest_overall_score(db, srv.server_id)
        all_axis_rows = _all_axes(db, srv.server_id)
        resp = _server_tier_response(srv, overall_score, all_axis_rows)

        if risk_tier is None or resp.published_risk_label.upper() == risk_tier.upper():
            results.append(resp)

    return results


@router.get(
    "/scoring/consumer/tier/summary",
    response_model=TierSummaryResponse,
)
def get_tier_summary(
    db: Session = Depends(get_session),
) -> TierSummaryResponse:
    """
    Return aggregate tier distribution: count and percentage of servers
    at each published risk tier.
    """
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    latest_q = (
        select(McpLlmAxisScore)
        .join(
            subq,
            (
                (McpLlmAxisScore.server_id == subq.c.server_id)
                & (McpLlmAxisScore.scored_at == subq.c.max_scored_at)
            ),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
    )
    latest_rows: List[McpLlmAxisScore] = list(
        db.execute(latest_q).scalars().all()
    )

    scored_ids = {r.server_id for r in latest_rows}
    scored_servers = len(scored_ids)
    unscored_servers = total - scored_servers

    # Count per tier
    tier_counts: dict = {}
    for row in latest_rows:
        tier = _derive_tier(row.p_top, row.p_critical)
        srv = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == row.server_id)
            .first()
        )
        published = tier
        if srv:
            gate = trust_gate(srv.url, srv.name, {"overall_risk": tier})
            if gate["capped"]:
                published = gate["published_overall_risk"]
        tier_counts[published] = tier_counts.get(published, 0) + 1

    all_tiers = [
        "CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL", "TRUSTED", "UNKNOWN"
    ]
    distribution = []
    for t in all_tiers:
        cnt = tier_counts.get(t, 0)
        pct = (
            round(cnt / scored_servers * 100, 2)
            if scored_servers > 0
            else 0.0
        )
        distribution.append(TierDistributionItem(tier=t, count=cnt, pct=pct))

    return TierSummaryResponse(
        generated_at=datetime.now(timezone.utc),
        total_servers=total,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        tier_distribution=distribution,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    from app.models import Base

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

    with TestSessionLocal() as sess:
        sess.add_all([
            McpServerRegistry(
                server_id="srv-1",
                name="stripe-payments",
                url="https://github.com/stripe/stripe-mcp",
                registry_source="github",
                risk_tier="HIGH",
                confidence=0.9,
            ),
            McpServerRegistry(
                server_id="srv-2",
                name="g00gle-search",
                url="https://github.com/g00gle/mcp-fake",
                registry_source="github",
                risk_tier="HIGH",
                confidence=0.8,
            ),
            McpServerRegistry(
                server_id="srv-3",
                name="unknown-tool",
                registry_source="npm",
                risk_tier=None,
                confidence=None,
            ),
        ])
        sess.commit()

        sess.add_all([
            # srv-1: verified Stripe publisher -> trust-gated to MEDIUM
            McpLlmAxisScore(
                id=1,
                server_id="srv-1",
                axis_name="overall_risk",
                label="HIGH",
                label_index=2,
                probs=None,
                p_top=0.65,
                p_critical=0.25,
                p_danger=0.05,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="v1",
                adapter_sha256=None,
                scored_at=now,
            ),
            McpLlmAxisScore(
                id=2,
                server_id="srv-1",
                axis_name="maintainer_trust",
                label="VERIFIED",
                label_index=0,
                probs=None,
                p_top=0.8,
                p_critical=0.0,
                p_danger=0.0,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="v1",
                adapter_sha256=None,
                scored_at=now,
            ),
            # srv-2: NOT verified (g00gle != google) -> masquerade flagged
            McpLlmAxisScore(
                id=3,
                server_id="srv-2",
                axis_name="overall_risk",
                label="HIGH",
                label_index=2,
                probs=None,
                p_top=0.65,
                p_critical=0.25,
                p_danger=0.05,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1",
                model_version="v1",
                adapter_sha256=None,
                scored_at=now,
            ),
            # srv-3: no scores (unscored)
        ])
        sess.commit()

    client = TestClient(test_app)

    # Test 1: get single server (verified Stripe -> capped)
    resp1 = client.get("/api/scoring/consumer/tier/srv-1")
    if resp1.status_code != 200:
        print(f"FAIL: get_server_tier returned {resp1.status_code}: {resp1.text}")
        _sys.exit(1)
    data1 = resp1.json()
    if data1["overall_risk_label"] != "HIGH":
        print(
            f"FAIL: expected overall_risk_label HIGH, got {data1['overall_risk_label']}"
        )
        _sys.exit(1)
    if data1["published_risk_label"] != "MEDIUM":
        print(
            f"FAIL: expected published_risk_label MEDIUM (trust-capped), got {data1['published_risk_label']}"
        )
        _sys.exit(1)
    if not data1["trust_info"]["trusted"]:
        print("FAIL: expected trust_info.trusted=True for Stripe server")
        _sys.exit(1)
    if not data1["trust_info"]["capped"]:
        print("FAIL: expected trust_info.capped=True")
        _sys.exit(1)

    # Test 2: masquerade detection (g00gle != google)
    resp2 = client.get("/api/scoring/consumer/tier/srv-2")
    if resp2.status_code != 200:
        print(
            f"FAIL: get_server_tier srv-2 returned {resp2.status_code}: {resp2.text}"
        )
        _sys.exit(1)
    data2 = resp2.json()
    if not data2["trust_info"]["masquerade_flag"]:
        print("FAIL: expected masquerade_flag=True for g00gle server")
        _sys.exit(1)
    if data2["published_risk_label"] != "HIGH":
        print(
            f"FAIL: unpublished server should keep HIGH, got {data2['published_risk_label']}"
        )
        _sys.exit(1)

    # Test 3: unscored server
    resp3 = client.get("/api/scoring/consumer/tier/srv-3")
    if resp3.status_code != 200:
        print(
            f"FAIL: get_server_tier srv-3 returned {resp3.status_code}: {resp3.text}"
        )
        _sys.exit(1)
    data3 = resp3.json()
    if data3["overall_risk_label"] != "UNKNOWN":
        print(
            f"FAIL: expected overall_risk_label UNKNOWN for unscored server, got {data3['overall_risk_label']}"
        )
        _sys.exit(1)

    # Test 4: unknown server returns 404
    resp4 = client.get("/api/scoring/consumer/tier/nonexistent")
    if resp4.status_code != 404:
        print(f"FAIL: unknown server should 404, got {resp4.status_code}")
        _sys.exit(1)

    # Test 5: list endpoint
    resp5 = client.get("/api/scoring/consumer/tier")
    if resp5.status_code != 200:
        print(f"FAIL: list_server_tiers returned {resp5.status_code}: {resp5.text}")
        _sys.exit(1)
    items = resp5.json()
    if len(items) != 3:
        print(f"FAIL: expected 3 servers, got {len(items)}")
        _sys.exit(1)

    # Test 6: tier filter
    resp6 = client.get("/api/scoring/consumer/tier?risk_tier=MEDIUM")
    if resp6.status_code != 200:
        print(f"FAIL: tier filter returned {resp6.status_code}")
        _sys.exit(1)
    filtered = resp6.json()
    if any(s["published_risk_label"] != "MEDIUM" for s in filtered):
        print("FAIL: filtered results should all be MEDIUM tier")
        _sys.exit(1)

    # Test 7: summary endpoint
    resp7 = client.get("/api/scoring/consumer/tier/summary")
    if resp7.status_code != 200:
        print(f"FAIL: summary returned {resp7.status_code}: {resp7.text}")
        _sys.exit(1)
    summary = resp7.json()
    if summary["total_servers"] != 3:
        print(f"FAIL: expected 3 total_servers, got {summary['total_servers']}")
        _sys.exit(1)
    if summary["scored_servers"] != 2:
        print(f"FAIL: expected 2 scored_servers, got {summary['scored_servers']}")
        _sys.exit(1)
    if summary["unscored_servers"] != 1:
        print(f"FAIL: expected 1 unscored_servers, got {summary['unscored_servers']}")
        _sys.exit(1)

    # Verify MEDIUM tier appears in distribution (trust-capped Stripe)
    tier_map = {d["tier"]: d["count"] for d in summary["tier_distribution"]}
    if tier_map.get("MEDIUM", 0) < 1:
        print(f"FAIL: expected at least 1 MEDIUM (Stripe trust-capped), got {tier_map}")
        _sys.exit(1)

    print("PASS")
