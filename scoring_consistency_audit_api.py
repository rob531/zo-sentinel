from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScore

router = APIRouter(tags=["scoring"])

class AxisData(BaseModel):
    axis_name: str
    label: str

class TrustOverrideResult(BaseModel):
    triggered: bool
    tier: Optional[str] = None
    reason: Optional[str] = None

class ScoringConsistencyResponse(BaseModel):
    server_id: str
    name: str
    sft_risk_tier: str
    derived_risk_tier: int
    overall_risk_axis: AxisData
    derived_axes: List[AxisData]
    trust_override: TrustOverrideResult
    consistency_score: float

class ConsistencyAnomalyItem(BaseModel):
    server_id: str
    name: str
    sft_risk_tier: str
    derived_risk_tier: int
    overall_risk_axis: AxisData
    derived_axes: List[AxisData]
    trust_override: TrustOverrideResult
    consistency_score: float
    derived_severity: str

class AnomalyListResponse(BaseModel):
    results: List[ConsistencyAnomalyItem]
    total: int

tier_values = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}

def convert_to_tier(label: Optional[str]) -> int:
    return tier_values.get(label, 0)

def compute_consistency(sft_tier: str, derived_tier: int, override_tier: Optional[str]) -> float:
    sft_tier_val = convert_to_tier(sft_tier)
    penalty = min(abs(sft_tier_val - derived_tier) / 3.0, 1.0)
    override_penalty = 0.2 if override_tier is not None and override_tier != sft_tier else 0.0
    return max(0.0, 1.0 - penalty - override_penalty)

@router.get("/servers/{server_id}/scoring-consistency", response_model=ScoringConsistencyResponse)
async def get_scoring_consistency(server_id: str, session=Depends(get_session)):
    stmt = select(MCPServerRegistry).where(MCPServerRegistry.server_id == server_id)
    result = session.execute(stmt)
    server = result.scalar_one_or_none()
    if not server:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Server not found")

    scores_stmt = select(MCPLLMAxisScore).where(MCPLLMAxisScore.server_id == server_id)
    scores_result = session.execute(scores_stmt)
    all_scores = list(scores_result.scalars().all())

    from trust_gating_override import trust_gate
    axes_dict = {s.axis_name: s.label for s in all_scores}
    trust_result = trust_gate(server.url, server.name, axes_dict)
    override_tier = trust_result[0] if isinstance(trust_result, tuple) else trust_result

    overall_score = next((s for s in all_scores if s.axis_name == 'overall_risk'), None)
    derived_scores = [s for s in all_scores if s.axis_name != 'overall_risk']
    derived_tier = max((convert_to_tier(s.label) for s in derived_scores), default=0)

    consistency = compute_consistency(server.risk_tier, derived_tier, override_tier)
    override_triggered = override_tier is not None and override_tier != server.risk_tier

    return ScoringConsistencyResponse(
        server_id=server.server_id,
        name=server.name,
        sft_risk_tier=server.risk_tier,
        derived_risk_tier=derived_tier,
        overall_risk_axis=AxisData(axis_name=overall_score.axis_name, label=overall_score.label) if overall_score else AxisData(axis_name='overall_risk', label='unknown'),
        derived_axes=[AxisData(axis_name=s.axis_name, label=s.label) for s in derived_scores],
        trust_override=TrustOverrideResult(
            triggered=override_triggered,
            tier=override_tier,
            reason=trust_result[1] if isinstance(trust_result, tuple) and len(trust_result) > 1 else None
        ),
        consistency_score=round(consistency, 2)
    )

@router.get("/scoring/consistency-anomalies", response_model=AnomalyListResponse)
async def get_consistency_anomalies(
    min_severity: str = Query("low", description="Minimum severity: low, medium, high, critical"),
    limit: int = Query(50, ge=1, le=500),
    session=Depends(get_session)
):
    min_severity_tier = convert_to_tier(min_severity)
    stmt = select(MCPServerRegistry).options(selectinload(MCPServerRegistry.llm_axis_scores)).join(
        MCPLLMAxisScore, MCPServerRegistry.server_id == MCPLLMAxisScore.server_id
    ).distinct()
    result = session.execute(stmt)
    servers = result.unique().scalars().all()

    from trust_gating_override import trust_gate
    anomalies = []
    for server in servers:
        all_scores = server.llm_axis_scores
        if not all_scores:
            continue
        axes_dict = {s.axis_name: s.label for s in all_scores}
        trust_result = trust_gate(server.url, server.name, axes_dict)
        override_tier = trust_result[0] if isinstance(trust_result, tuple) else trust_result

        overall_score = next((s for s in all_scores if s.axis_name == 'overall_risk'), None)
        derived_scores = [s for s in all_scores if s.axis_name != 'overall_risk']
        derived_tier = max((convert_to_tier(s.label) for s in derived_scores), default=0)

        consistency = compute_consistency(server.risk_tier, derived_tier, override_tier)
        override_triggered = override_tier is not None and override_tier != server.risk_tier

        if consistency < 1.0 and derived_tier >= min_severity_tier:
            anomalies.append(ConsistencyAnomalyItem(
                server_id=server.server_id,
                name=server.name,
                sft_risk_tier=server.risk_tier,
                derived_risk_tier=derived_tier,
                overall_risk_axis=AxisData(axis_name=overall_score.axis_name, label=overall_score.label) if overall_score else AxisData(axis_name='overall_risk', label='unknown'),
                derived_axes=[AxisData(axis_name=s.axis_name, label=s.label) for s in derived_scores],
                trust_override=TrustOverrideResult(
                    triggered=override_triggered,
                    tier=override_tier,
                    reason=trust_result[1] if isinstance(trust_result, tuple) and len(trust_result) > 1 else None
                ),
                consistency_score=round(consistency, 2),
                derived_severity=overall_score.label if overall_score else 'unknown'
            ))

    anomalies.sort(key=lambda x: x.consistency_score)
    return AnomalyListResponse(results=anomalies[:limit], total=len(anomalies))

if __name__ == "__main__":
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    def mock_trust_gate(url, name, axes):
        if 'override' in name.lower():
            return ('critical', 'manual override')
        return ('medium', 'default')

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.bind = engine
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    session = TestingSessionLocal()

    session.add(MCPServerRegistry(server_id="srv1", name="ConsistentServer", url="https://consistent.example.com", risk_tier="low"))
    session.add(MCPServerRegistry(server_id="srv2", name="MismatchServer", url="https://mismatch.example.com", risk_tier="low"))
    session.add(MCPServerRegistry(server_id="srv3", name="OverrideServer", url="https://override.example.com", risk_tier="high"))

    now = datetime.utcnow()
    session.add(MCPLLMAxisScore(server_id="srv1", axis_name="overall_risk", label="low", p_top=0.1, p_critical=0.0, escalated=False, scored_at=now))
    for ax in ["auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]:
        session.add(MCPLLMAxisScore(server_id="srv1", axis_name=ax, label="low", p_top=0.1, p_critical=0.0, escalated=False, scored_at=now))

    session.add(MCPLLMAxisScore(server_id="srv2", axis_name="overall_risk", label="high", p_top=0.8, p_critical=0.2, escalated=True, scored_at=now))
    for ax in ["auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]:
        label = "high" if ax == "capability_breadth" else "low"
        session.add(MCPLLMAxisScore(server_id="srv2", axis_name=ax, label=label, p_top=0.1, p_critical=0.0, escalated=False, scored_at=now))

    session.add(MCPLLMAxisScore(server_id="srv3", axis_name="overall_risk", label="high", p_top=0.7, p_critical=0.1, escalated=False, scored_at=now))
    for ax in ["auth_strength", "capability_breadth", "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface"]:
        session.add(MCPLLMAxisScore(server_id="srv3", axis_name=ax, label="medium", p_top=0.3, p_critical=0.05, escalated=False, scored_at=now))

    session.commit()

    with patch("app.api.scoring_consistency_audit_api.trust_gate", mock_trust_gate):
        from app.api.scoring_consistency_audit_api import router as api_router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(api_router)
        client = TestClient(app)

        resp1 = client.get("/servers/srv1/scoring-consistency")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["consistency_score"] == 1.0, f"Server 1: expected 1.0, got {data1['consistency_score']}"

        resp2 = client.get("/servers/srv2/scoring-consistency")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["consistency_score"] == 0.7, f"Server 2: expected 0.7, got {data2['consistency_score']}"

        resp3 = client.get("/servers/srv3/scoring-consistency")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["consistency_score"] == 0.8, f"Server 3: expected 0.8, got {data3['consistency_score']}"

        anomalies_resp = client.get("/scoring/consistency-anomalies?min_severity=low&limit=50")
        assert anomalies_resp.status_code == 200
        anomalies = anomalies_resp.json()["results"]
        assert len(anomalies) == 3, f"Expected 3 anomalies, got {len(anomalies)}"

    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
    print("PASS")