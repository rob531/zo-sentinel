# deps: fastapi, pydantic, sqlalchemy
"""MCP Risk Tier Scoring Consumer.

Consumes axis scores from mcp_llm_axis_scores, derives per-server risk tiers,
and surfaces the computed verdicts. Reads from the app Postgres tier via
get_session + SQLAlchemy models.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["mcp_risk_tier_scoring_consumer"])


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
AXIS_WEIGHTS: Dict[str, float] = {
    "overall_risk": 0.25,
    "auth_strength": 0.12,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.18,
    "network_egress": 0.15,
    "maintainer_trust": 0.12,
    "exploit_surface": 0.08,
}

RISK_TIER_THRESHOLDS: List[tuple[float, str]] = [
    (75, "TRUSTED_GENERAL"),
    (60, "TRUSTED_RESEARCH"),
    (45, "ENTERPRISE_CONTROLLED"),
    (30, "CAUTION_LIMITED"),
    (15, "HIGH_RISK_ISOLATED"),
    (0, "KNOWN_THREAT"),
]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _compute_tier_contribution(p_top: float, p_critical: float, p_danger: float) -> float:
    return round((p_top * 100) + (p_critical * 60) + (p_danger * 20), 2)


def _compute_overall_score(axes: List[Dict[str, float]]) -> float:
    weighted_sum = 0.0
    for axis in axes:
        weight = AXIS_WEIGHTS.get(axis["axis_name"], 0.1)
        weighted_sum += weight * axis["tier_contribution"]
    return round(min(weighted_sum, 100.0), 2)


def _map_to_risk_tier(score: float) -> str:
    for threshold, tier in RISK_TIER_THRESHOLDS:
        if score > threshold:
            return tier
    return "KNOWN_THREAT"


def _compute_confidence(axes: List[Dict[str, float]], total_axes: int = 7) -> float:
    if len(axes) < total_axes:
        return round(len(axes) / total_axes * 0.8, 2)
    return 0.95


def _fetch_axis_scores(db: Session, server_id: str) -> List[Dict[str, float]]:
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.server_id == server_id)
    records = db.execute(stmt).scalars().all()
    axis_dict: Dict[str, Dict[str, float]] = {}
    for record in records:
        contribution = _compute_tier_contribution(
            record.p_top or 0.0,
            record.p_critical or 0.0,
            record.p_danger or 0.0,
        )
        axis_dict[record.axis_name] = {
            "axis_name": record.axis_name,
            "label": record.label or record.axis_name,
            "p_top": record.p_top,
            "p_critical": record.p_critical,
            "p_danger": record.p_danger,
            "tier_contribution": contribution,
        }
    return [axis_dict[name] for name in AXIS_WEIGHTS if name in axis_dict]


# --------------------------------------------------------------------------- #
# Response shapes
# --------------------------------------------------------------------------- #
class AxisScoreResponse(BaseModel):
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    tier_contribution: float


class ServerRiskTierResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    computed_overall_score: float
    risk_tier: str
    confidence: float
    axes: List[AxisScoreResponse]
    computed_at: datetime
    score_source: str = "llm_axis_composite"


class BatchComputeRequest(BaseModel):
    server_ids: Optional[List[str]] = Field(
        default=None,
        description="List of server IDs to recompute. If None, recomputes all.",
    )


class BatchComputeResponse(BaseModel):
    computed_count: int
    tier_distribution: Dict[str, int]
    computed_at: datetime


class ScoringHealthResponse(BaseModel):
    status: str
    total_servers: int
    scored_servers: int
    unscored_servers: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/risk-tier",
    response_model=ServerRiskTierResponse,
)
def get_server_risk_tier(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerRiskTierResponse:
    """Return the computed risk tier for a single server based on its axis scores."""
    server = db.get(McpServerRegistry, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axes_list = _fetch_axis_scores(db, server_id)
    overall_score = _compute_overall_score(axes_list)
    risk_tier = _map_to_risk_tier(overall_score)
    confidence = _compute_confidence(axes_list)

    axes_response = [
        AxisScoreResponse(**a) for a in sorted(
            axes_list, key=lambda x: list(AXIS_WEIGHTS.keys()).index(x["axis_name"])
        )
    ]

    return ServerRiskTierResponse(
        server_id=server_id,
        server_name=server.name,
        computed_overall_score=overall_score,
        risk_tier=risk_tier,
        confidence=confidence,
        axes=axes_response,
        computed_at=datetime.now(timezone.utc),
    )


@router.post(
    "/risk-tier/compute",
    response_model=BatchComputeResponse,
)
def compute_all_risk_tiers(
    request: BatchComputeRequest,
    db: Session = Depends(get_session),
) -> BatchComputeResponse:
    """Recompute and persist risk tiers for all servers (or a specific set)."""
    now = datetime.now(timezone.utc)

    if request.server_ids:
        servers = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id.in_(request.server_ids))
            .all()
        )
    else:
        servers = db.query(McpServerRegistry).all()

    tier_distribution: Dict[str, int] = {}
    computed_count = 0

    for server in servers:
        axes_list = _fetch_axis_scores(db, server.server_id)
        overall_score = _compute_overall_score(axes_list)
        risk_tier = _map_to_risk_tier(overall_score)

        stmt_update = (
            update(McpServerRegistry)
            .where(McpServerRegistry.server_id == server.server_id)
            .values(
                risk_tier=risk_tier,
                verdict=str(round(overall_score, 2)),
                confidence=_compute_confidence(axes_list),
                last_assessed=now,
            )
        )
        db.execute(stmt_update)
        tier_distribution[risk_tier] = tier_distribution.get(risk_tier, 0) + 1
        computed_count += 1

    db.commit()

    return BatchComputeResponse(
        computed_count=computed_count,
        tier_distribution=tier_distribution,
        computed_at=now,
    )


@router.get("/risk-tier/health", response_model=ScoringHealthResponse)
def scoring_health(db: Session = Depends(get_session)) -> ScoringHealthResponse:
    """Return basic health stats for the scoring consumer."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0
    scored = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .scalar() or 0
    )
    return ScoringHealthResponse(
        status="ok",
        total_servers=total,
        scored_servers=scored,
        unscored_servers=total - scored,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    from app.db import Base

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
                server_id="srv-high", name="High Trust Server",
                risk_tier="TRUSTED_GENERAL",
            ),
            McpServerRegistry(
                server_id="srv-mid", name="Mid Trust Server",
                risk_tier="ENTERPRISE_CONTROLLED",
            ),
            McpServerRegistry(
                server_id="srv-low", name="Low Trust Server",
                risk_tier="HIGH_RISK_ISOLATED",
            ),
            McpServerRegistry(
                server_id="srv-none", name="No Score Server",
                risk_tier=None,
            ),
        ])
        sess.commit()

        axes_config = [
            ("srv-high", [
                ("overall_risk", "TRUSTED", 0.90, 0.08, 0.02),
                ("auth_strength", "STRONG", 0.85, 0.10, 0.05),
                ("capability_breadth", "BROAD", 0.80, 0.15, 0.05),
                ("data_sensitivity", "LOW", 0.88, 0.10, 0.02),
                ("network_egress", "RESTRICTED", 0.92, 0.06, 0.02),
                ("maintainer_trust", "HIGH", 0.87, 0.10, 0.03),
                ("exploit_surface", "NARROW", 0.85, 0.12, 0.03),
            ]),
            ("srv-mid", [
                ("overall_risk", "MEDIUM", 0.55, 0.30, 0.15),
                ("auth_strength", "MODERATE", 0.50, 0.35, 0.15),
                ("capability_breadth", "MEDIUM", 0.60, 0.30, 0.10),
                ("data_sensitivity", "MEDIUM", 0.45, 0.35, 0.20),
                ("network_egress", "MODERATE", 0.55, 0.30, 0.15),
                ("maintainer_trust", "MODERATE", 0.50, 0.35, 0.15),
                ("exploit_surface", "MODERATE", 0.60, 0.25, 0.15),
            ]),
            ("srv-low", [
                ("overall_risk", "CRITICAL", 0.10, 0.20, 0.70),
                ("auth_strength", "WEAK", 0.15, 0.25, 0.60),
                ("capability_breadth", "BROAD", 0.20, 0.30, 0.50),
                ("data_sensitivity", "HIGH", 0.10, 0.25, 0.65),
                ("network_egress", "OPEN", 0.08, 0.22, 0.70),
                ("maintainer_trust", "LOW", 0.12, 0.28, 0.60),
                ("exploit_surface", "WIDE", 0.15, 0.25, 0.60),
            ]),
        ]

        for srv_id, axes in axes_config:
            for axis_name, label, p_top, p_critical, p_danger in axes:
                sess.add(McpLlmAxisScore(
                    server_id=srv_id,
                    axis_name=axis_name,
                    label=label,
                    p_top=p_top,
                    p_critical=p_critical,
                    p_danger=p_danger,
                    model_version="v1",
                    scored_at=now,
                ))
        sess.commit()

    client = TestClient(test_app)

    # Test 1: health endpoint
    resp = client.get("/api/risk-tier/health")
    if resp.status_code != 200:
        print(f"FAIL: health returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    health = resp.json()
    if health["total_servers"] != 4:
        print(f"FAIL: expected 4 total_servers, got {health['total_servers']}")
        sys.exit(1)
    if health["scored_servers"] != 3:
        print(f"FAIL: expected 3 scored_servers, got {health['scored_servers']}")
        sys.exit(1)

    # Test 2: srv-high -> TRUSTED_GENERAL
    resp = client.get("/api/servers/srv-high/risk-tier")
    if resp.status_code != 200:
        print(f"FAIL: srv-high returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["risk_tier"] != "TRUSTED_GENERAL":
        print(f"FAIL: srv-high expected TRUSTED_GENERAL, got {data['risk_tier']}")
        sys.exit(1)
    if len(data["axes"]) != 7:
        print(f"FAIL: srv-high expected 7 axes, got {len(data['axes'])}")
        sys.exit(1)

    # Test 3: srv-mid -> ENTERPRISE_CONTROLLED
    resp = client.get("/api/servers/srv-mid/risk-tier")
    if resp.status_code != 200:
        print(f"FAIL: srv-mid returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["risk_tier"] != "ENTERPRISE_CONTROLLED":
        print(f"FAIL: srv-mid expected ENTERPRISE_CONTROLLED, got {data['risk_tier']}")
        sys.exit(1)

    # Test 4: srv-low -> HIGH_RISK_ISOLATED
    resp = client.get("/api/servers/srv-low/risk-tier")
    if resp.status_code != 200:
        print(f"FAIL: srv-low returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["risk_tier"] != "HIGH_RISK_ISOLATED":
        print(f"FAIL: srv-low expected HIGH_RISK_ISOLATED, got {data['risk_tier']}")
        sys.exit(1)

    # Test 5: 404 for unknown server
    resp = client.get("/api/servers/unknown-server/risk-tier")
    if resp.status_code != 404:
        print(f"FAIL: unknown server expected 404, got {resp.status_code}")
        sys.exit(1)

    # Test 6: batch compute
    resp = client.post("/api/risk-tier/compute", json={"server_ids": ["srv-high", "srv-mid"]})
    if resp.status_code != 200:
        print(f"FAIL: batch compute returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    batch = resp.json()
    if batch["computed_count"] != 2:
        print(f"FAIL: batch expected 2 computed, got {batch['computed_count']}")
        sys.exit(1)
    if "TRUSTED_GENERAL" not in batch["tier_distribution"]:
        print(f"FAIL: batch missing TRUSTED_GENERAL tier: {batch['tier_distribution']}")
        sys.exit(1)

    # Test 7: compute all
    resp = client.post("/api/risk-tier/compute", json={})
    if resp.status_code != 200:
        print(f"FAIL: compute all returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    batch_all = resp.json()
    if batch_all["computed_count"] != 4:
        print(f"FAIL: compute all expected 4, got {batch_all['computed_count']}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
