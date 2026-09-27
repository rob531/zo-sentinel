# deps: fastapi, pydantic, sqlalchemy
"""router.py -- FastAPI surface for risk_tier_scorer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import AxisScoreItem, RiskScoreResult, batch_risk_scores, compute_risk_score

router = APIRouter(prefix="/api", tags=["risk_tier_scorer"])


class AxisScoreResponse(BaseModel):
    axis_name: str
    label_index: int
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    weight: float


class RiskScoreResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    overall_score: float
    confidence: float
    risk_tier: str
    axes: List[AxisScoreResponse]
    evidence: dict


class BatchRiskScoreRequest(BaseModel):
    server_ids: List[str]


@router.get("/risk_tier_scorer/{server_id}", response_model=RiskScoreResponse)
def get_risk_score(
    server_id: str,
    db: Session = Depends(get_session),
) -> RiskScoreResponse:
    """Return computed risk score and tier for a single server."""
    result = compute_risk_score(db, server_id)
    return RiskScoreResponse(
        server_id=result.server_id,
        server_name=result.server_name,
        overall_score=result.overall_score,
        confidence=result.confidence,
        risk_tier=result.risk_tier,
        axes=[AxisScoreResponse(**ax) for ax in result.axes],
        evidence=result.evidence,
    )


@router.post("/risk_tier_scorer/batch", response_model=List[RiskScoreResponse])
def post_batch_risk_scores(
    payload: BatchRiskScoreRequest,
    db: Session = Depends(get_session),
) -> List[RiskScoreResponse]:
    """Return computed risk scores for multiple servers."""
    results = batch_risk_scores(db, payload.server_ids)
    return [
        RiskScoreResponse(
            server_id=r.server_id,
            server_name=r.server_name,
            overall_score=r.overall_score,
            confidence=r.confidence,
            risk_tier=r.risk_tier,
            axes=[AxisScoreResponse(**ax) for ax in r.axes],
            evidence=r.evidence,
        )
        for r in results
    ]


@router.get("/risk_tier_scorer", response_model=RiskScoreResponse)
def get_risk_score_query(
    server_id: str = Query(..., description="Server identifier"),
    db: Session = Depends(get_session),
) -> RiskScoreResponse:
    """Return computed risk score and tier for a server (query-param variant)."""
    return get_risk_score(server_id, db)


# ---------------------------------------------------------------------------
# __main__ self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)

    # Seed test data
    s = TestingSession()
    from app.models import McpServerRegistry, McpLlmAxisScore

    s.add(McpServerRegistry(
        server_id="srv-t1",
        name="Test Server Alpha",
        registry_source="test",
        url="http://example.com/alpha",
        description="Test alpha",
        confidence=0.9,
        first_seen=now,
        last_seen=now,
        last_scanned=now,
        last_assessed=now,
        meta={},
        scan_count=1,
        trust_score=0.8,
        verdict="clean",
        verdict_reasoning="none",
        risk_tier="low",
    ))
    s.add(McpLlmAxisScore(
        server_id="srv-t1",
        axis_name="overall_risk",
        label="LOW",
        label_index=2,
        probs="[]",
        p_top=0.85,
        p_critical=0.05,
        p_danger=0.10,
        escalated=False,
        decision_rule_version="v1",
        model_version="v1",
        adapter_sha256="abc123",
        scored_at=now,
    ))
    s.add(McpLlmAxisScore(
        server_id="srv-t1",
        axis_name="auth_strength",
        label="MEDIUM",
        label_index=4,
        probs="[]",
        p_top=0.60,
        p_critical=0.15,
        p_danger=0.25,
        escalated=True,
        decision_rule_version="v1",
        model_version="v1",
        adapter_sha256="abc123",
        scored_at=now,
    ))
    s.commit()
    s.close()

    client = TestClient(app)

    # Test single server GET
    r = client.get("/api/risk_tier_scorer/srv-t1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["server_id"] == "srv-t1"
    assert body["server_name"] == "Test Server Alpha"
    assert body["overall_score"] > 0
    assert body["risk_tier"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "MINIMAL")
    assert len(body["axes"]) == 2
    assert body["evidence"]["axes_analyzed"] == 2

    # Test query-param variant
    r2 = client.get("/api/risk_tier_scorer?server_id=srv-t1")
    assert r2.status_code == 200, r2.text
    assert r2.json()["server_id"] == "srv-t1"

    # Test batch
    r3 = client.post("/api/risk_tier_scorer/batch", json={"server_ids": ["srv-t1"]})
    assert r3.status_code == 200, r3.text
    assert len(r3.json()) == 1

    # Test unknown server
    r4 = client.get("/api/risk_tier_scorer/unknown-srv")
    assert r4.status_code == 200, r4.text
    body4 = r4.json()
    assert body4["risk_tier"] == "UNKNOWN"
    assert body4["overall_score"] == 0.0

    print("PASS")
    sys.exit(0)
