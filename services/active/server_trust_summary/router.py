# deps: fastapi, pydantic, sqlalchemy
"""Server Trust Summary API.

GET /api/servers/{server_id}/trust-summary
  Returns trust summary for a server: registry metadata, composite score from
  axis scores, and gating decision.

Auth: public.
Data: app Postgres via get_session + SQLAlchemy ORM on
  mcp_server_registry, mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_trust_summary"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisScoreOut(BaseModel):
    axis_name: str = Field(..., description="Axis name, e.g. overall_risk")
    label: Optional[str] = Field(None, description="Risk label assigned by model")
    p_top: float = Field(..., description="Probability of top/confident class")
    p_critical: float = Field(..., description="Probability of critical risk class")
    p_danger: float = Field(..., description="Probability of danger class")
    probs: list[float] = Field(default_factory=list, description="Full probability distribution")

    model_config = {"from_attributes": True}


class TrustGatingOut(BaseModel):
    is_trusted: bool = Field(..., description="Whether server passes trust gate")
    gate_source: str = Field(..., description="Source of gating decision")
    gate_reason: str = Field(..., description="Human-readable gating rationale")

    model_config = {"from_attributes": True}


class ServerTrustSummaryResponse(BaseModel):
    server_id: str = Field(..., description="Unique server identifier")
    name: str = Field(..., description="Server display name")
    registry_source: str = Field(..., description="Source registry")
    risk_tier: Optional[str] = Field(None, description="Risk tier classification")
    verdict: Optional[str] = Field(None, description="Current verdict")
    confidence: Optional[float] = Field(None, description="Confidence score")
    trust_score: Optional[float] = Field(None, description="Registry trust score")
    composite_score: float = Field(..., ge=0, le=100, description="Weighted composite from axes")
    axes: list[AxisScoreOut] = Field(default_factory=list, description="Per-axis scores")
    gating: TrustGatingOut = Field(..., description="Trust gating decision")
    criteria_version: str = Field(..., description="Criteria version used")
    last_assessed: Optional[str] = Field(None, description="ISO 8601 last assessed time")
    first_seen: Optional[str] = Field(None, description="ISO 8601 first seen time")
    scan_count: int = Field(0, description="Number of scans performed")

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Logic
# --------------------------------------------------------------------------- #

# Weights for each axis when computing composite score
AXIS_WEIGHTS = {
    "overall_risk": 0.20,
    "auth_strength": 0.12,
    "capability_breadth": 0.10,
    "data_sensitivity": 0.15,
    "network_egress": 0.13,
    "maintainer_trust": 0.15,
    "exploit_surface": 0.15,
}

TRUST_SCORE_THRESHOLD = 75.0
COMPOSITE_TRUST_THRESHOLD = 70.0
COMPOSITE_UNTRUST_THRESHOLD = 40.0


def compute_composite_score(axis_scores: list[McpLlmAxisScore]) -> float:
    """Compute weighted composite score from axis probabilities."""
    weighted_sum = 0.0
    total_weight = 0.0

    for score in axis_scores:
        weight = AXIS_WEIGHTS.get(score.axis_name, 0.0)
        if weight > 0 and score.p_top is not None:
            # p_top is in [0,1]; convert to 0-100 scale
            risk_score = float(score.p_top) * 100
            weighted_sum += risk_score * weight
            total_weight += weight

    if total_weight == 0:
        return 50.0
    return round(weighted_sum / total_weight, 2)


def determine_gating(
    server: McpServerRegistry,
    composite_score: float
) -> TrustGatingOut:
    """Determine trust gating outcome based on registry and composite score."""
    # Registry trust_score override
    if server.trust_score is not None and server.trust_score >= TRUST_SCORE_THRESHOLD:
        return TrustGatingOut(
            is_trusted=True,
            gate_source="trust_score_override",
            gate_reason=f"Registry trust_score {server.trust_score} >= {TRUST_SCORE_THRESHOLD}"
        )

    # Composite score thresholds
    if composite_score >= COMPOSITE_TRUST_THRESHOLD:
        return TrustGatingOut(
            is_trusted=True,
            gate_source="composite_score",
            gate_reason=f"Composite score {composite_score} >= {COMPOSITE_TRUST_THRESHOLD}"
        )

    if composite_score < COMPOSITE_UNTRUST_THRESHOLD:
        return TrustGatingOut(
            is_trusted=False,
            gate_source="composite_score",
            gate_reason=f"Composite score {composite_score} < {COMPOSITE_UNTRUST_THRESHOLD}"
        )

    # Intermediate zone
    return TrustGatingOut(
        is_trusted=False,
        gate_source="composite_score",
        gate_reason=f"Composite score {composite_score} in [{COMPOSITE_UNTRUST_THRESHOLD}, {COMPOSITE_TRUST_THRESHOLD})"
    )


def get_trust_summary(db: Session, server_id: str) -> ServerTrustSummaryResponse:
    """Build trust summary for a server."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    # Fetch axis scores
    axis_scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    # Build axis output
    axes = [
        AxisScoreOut(
            axis_name=score.axis_name,
            label=score.label,
            p_top=score.p_top or 0.0,
            p_critical=score.p_critical or 0.0,
            p_danger=score.p_danger or 0.0,
            probs=score.probs if isinstance(score.probs, list) else []
        )
        for score in axis_scores
    ]

    # Compute composite and gating
    composite_score = compute_composite_score(axis_scores)
    gating = determine_gating(server, composite_score)

    def _iso(val: Optional[datetime]) -> Optional[str]:
        return val.isoformat() if val else None

    return ServerTrustSummaryResponse(
        server_id=server.server_id,
        name=server.name,
        registry_source=server.registry_source,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        confidence=server.confidence,
        trust_score=server.trust_score,
        composite_score=composite_score,
        axes=axes,
        gating=gating,
        criteria_version="1.0",
        last_assessed=_iso(server.last_assessed),
        first_seen=_iso(server.first_seen),
        scan_count=server.scan_count or 0,
    )


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/servers/{server_id}/trust-summary",
    response_model=ServerTrustSummaryResponse
)
def trust_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerTrustSummaryResponse:
    """Return trust summary for a server."""
    return get_trust_summary(db, server_id)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Ensure repo root is on sys.path for app imports
    _repo_root = Path(__file__).resolve().parents[3]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Build local FastAPI app for self-test
    test_app = FastAPI()
    test_app.include_router(router)

    # In-memory SQLite with StaticPool
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Seed test data
    now = datetime.now(timezone.utc)

    with TestSession() as sess:
        # Server 1: high trust
        srv1 = McpServerRegistry(
            server_id="srv-trust-001",
            name="Trusted Alpha",
            registry_source="test",
            url="http://alpha.test",
            risk_tier="LOW",
            verdict="CLEAN",
            confidence=0.95,
            trust_score=88.0,
            first_seen=now,
            last_assessed=now,
            scan_count=15,
        )
        sess.add(srv1)

        # Server 2: medium risk
        srv2 = McpServerRegistry(
            server_id="srv-trust-002",
            name="Medium Beta",
            registry_source="test",
            url="http://beta.test",
            risk_tier="MEDIUM",
            verdict="SUSPICIOUS",
            confidence=0.70,
            trust_score=55.0,
            first_seen=now,
            last_assessed=now,
            scan_count=5,
        )
        sess.add(srv2)

        sess.flush()

        # Axis scores for srv1 (high trust)
        for i, axis in enumerate(["overall_risk", "auth_strength", "capability_breadth"]):
            sess.add(McpLlmAxisScore(
                server_id="srv-trust-001",
                axis_name=axis,
                label="low_risk",
                label_index=i,
                p_top=0.85,
                p_critical=0.08,
                p_danger=0.07,
                probs=[0.85, 0.08, 0.07],
                model_version="v1-test",
                decision_rule_version="v1",
                scored_at=now,
            ))

        # Axis scores for srv2 (medium risk)
        for i, axis in enumerate(["overall_risk", "data_sensitivity", "network_egress"]):
            sess.add(McpLlmAxisScore(
                server_id="srv-trust-002",
                axis_name=axis,
                label="medium_risk",
                label_index=i,
                p_top=0.55,
                p_critical=0.25,
                p_danger=0.20,
                probs=[0.55, 0.25, 0.20],
                model_version="v1-test",
                decision_rule_version="v1",
                scored_at=now,
            ))

        sess.commit()

    def _override() -> Generator[Session, None, None]:
        with TestSession() as s:
            yield s

    # Override using app.main:app
    from app.main import app as _app_instance
    _app_instance.dependency_overrides[get_session] = _override

    client = TestClient(test_app)

    # Test server 1 (trusted via trust_score)
    resp1 = client.get("/api/servers/srv-trust-001/trust-summary")
    assert resp1.status_code == 200, f"srv-trust-001: {resp1.status_code} {resp1.text}"
    data1 = resp1.json()
    assert len(data1["axes"]) == 3, f"srv-trust-001 axes: {len(data1['axes'])}"
    assert 0 <= data1["composite_score"] <= 100, f"composite_score: {data1['composite_score']}"
    assert isinstance(data1["gating"]["is_trusted"], bool)
    assert data1["gating"]["is_trusted"] is True, f"srv-trust-001 should be trusted: {data1['gating']}"
    assert data1["server_id"] == "srv-trust-001"
    assert data1["trust_score"] == 88.0

    # Test server 2 (not trusted, medium composite)
    resp2 = client.get("/api/servers/srv-trust-002/trust-summary")
    assert resp2.status_code == 200, f"srv-trust-002: {resp2.status_code} {resp2.text}"
    data2 = resp2.json()
    assert len(data2["axes"]) == 3, f"srv-trust-002 axes: {len(data2['axes'])}"
    assert 0 <= data2["composite_score"] <= 100, f"composite_score: {data2['composite_score']}"
    assert isinstance(data2["gating"]["is_trusted"], bool)
    # trust_score=55 < 75, composite < 70 -> not trusted
    assert data2["gating"]["is_trusted"] is False, f"srv-trust-002 should not be trusted: {data2['gating']}"

    # Test 404
    resp3 = client.get("/api/servers/nonexistent/trust-summary")
    assert resp3.status_code == 404, f"nonexistent: expected 404, got {resp3.status_code}"

    print("PASS")
    sys.exit(0)
