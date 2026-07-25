from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores

router = APIRouter(tags=["risk-tier"])

AXIS_NAMES = [
    "security", "reliability", "performance", "scalability",
    "maintainability", "availability", "compliance"
]

class AxisScoreResponse(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    label_index: int

class ServerRiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    axes: dict[str, AxisScoreResponse]
    criteria_version: str
    scored_at: str

class RiskTierBreakdownItem(BaseModel):
    server_id: str
    risk_tier: str
    top_axis: Optional[str] = None
    top_axis_label: Optional[str] = None

class RiskTierBreakdownResponse(BaseModel):
    items: list[RiskTierBreakdownItem]
    total: int
    page: int
    page_size: int

class RiskTierSummaryResponse(BaseModel):
    tiers: dict[str, int]
    total: int

def compute_risk_tier(axes: dict[str, AxisScoreResponse]) -> str:
    for axis_score in axes.values():
        if axis_score.p_critical >= 0.5:
            return "HIGH_RISK_ISOLATED"
    return "LOW_RISK"

def get_axis_from_row(row, axis_name: str) -> Optional[AxisScoreResponse]:
    label = getattr(row, f"{axis_name}_label", None)
    if label is None:
        return None
    return AxisScoreResponse(
        label=label,
        p_top=getattr(row, f"{axis_name}_p_top", 0.0),
        p_critical=getattr(row, f"{axis_name}_p_critical", 0.0),
        p_danger=getattr(row, f"{axis_name}_p_danger", 0.0),
        label_index=getattr(row, f"{axis_name}_label_index", 0)
    )

@router.get("/servers/{server_id}/risk-tier", response_model=ServerRiskTierResponse)
async def get_server_risk_tier(server_id: str, session=Depends(get_session)):
    row = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Server not found or not scored")
    
    axes = {}
    for axis_name in AXIS_NAMES:
        axis_score = get_axis_from_row(row, axis_name)
        if axis_score:
            axes[axis_name] = axis_score
    
    return ServerRiskTierResponse(
        server_id=server_id,
        risk_tier=compute_risk_tier(axes),
        axes=axes,
        criteria_version=row.criteria_version,
        scored_at=row.scored_at.isoformat()
    )

@router.get("/risk-tier/breakdown", response_model=RiskTierBreakdownResponse)
async def get_risk_tier_breakdown(
    page: int = 1,
    page_size: int = 50,
    session=Depends(get_session)
):
    query = session.query(MCPLLMAxisScores)
    total = query.count()
    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()
    
    items = []
    for row in rows:
        axes = {}
        for axis_name in AXIS_NAMES:
            axis_score = get_axis_from_row(row, axis_name)
            if axis_score:
                axes[axis_name] = axis_score
        
        risk_tier = compute_risk_tier(axes)
        top_axis_name = max(axes, key=lambda k: axes[k].p_top) if axes else None
        top_axis_label = axes[top_axis_name].label if top_axis_name else None
        
        items.append(RiskTierBreakdownItem(
            server_id=row.server_id,
            risk_tier=risk_tier,
            top_axis=top_axis_name,
            top_axis_label=top_axis_label
        ))
    
    return RiskTierBreakdownResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size
    )

@router.get("/risk-tier/summary", response_model=RiskTierSummaryResponse)
async def get_risk_tier_summary(session=Depends(get_session)):
    rows = session.query(MCPLLMAxisScores).all()
    tier_counts = {"LOW_RISK": 0, "HIGH_RISK_ISOLATED": 0}
    
    for row in rows:
        axes = {}
        for axis_name in AXIS_NAMES:
            axis_score = get_axis_from_row(row, axis_name)
            if axis_score:
                axes[axis_name] = axis_score
        risk_tier = compute_risk_tier(axes)
        tier_counts[risk_tier] = tier_counts.get(risk_tier, 0) + 1
    
    return RiskTierSummaryResponse(
        tiers=tier_counts,
        total=len(rows)
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    
    app = FastAPI()
    app.include_router(router)
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    
    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    db = TestingSession()
    db.add(MCPServerRegistry(server_id="server1", server_name="Test Server 1"))
    db.add(MCPServerRegistry(server_id="server2", server_name="Test Server 2"))
    db.add(MCPServerRegistry(server_id="server3", server_name="Test Server 3"))
    
    now = datetime.utcnow()
    row1 = MCPLLMAxisScores(
        server_id="server1", criteria_version="v1", scored_at=now,
        security_label="LOW", security_p_top=0.2, security_p_critical=0.1, security_p_danger=0.1, security_label_index=0,
        reliability_label="LOW", reliability_p_top=0.3, reliability_p_critical=0.1, reliability_p_danger=0.1, reliability_label_index=0,
        performance_label="LOW", performance_p_top=0.1, performance_p_critical=0.1, performance_p_danger=0.1, performance_label_index=0,
        scalability_label="LOW", scalability_p_top=0.2, scalability_p_critical=0.1, scalability_p_danger=0.1, scalability_label_index=0,
        maintainability_label="LOW", maintainability_p_top=0.1, maintainability_p_critical=0.1, maintainability_p_danger=0.1, maintainability_label_index=0,
        availability_label="LOW", availability_p_top=0.2, availability_p_critical=0.1, availability_p_danger=0.1, availability_label_index=0,
        compliance_label="LOW", compliance_p_top=0.1, compliance_p_critical=0.1, compliance_p_danger=0.1, compliance_label_index=0
    )
    db.add(row1)
    
    row2 = MCPLLMAxisScores(
        server_id="server2", criteria_version="v1", scored_at=now,
        security_label="CRITICAL", security_p_top=0.1, security_p_critical=0.8, security_p_danger=0.1, security_label_index=3,
        reliability_label="LOW", reliability_p_top=0.3, reliability_p_critical=0.1, reliability_p_danger=0.1, reliability_label_index=0,
        performance_label="LOW", performance_p_top=0.1, performance_p_critical=0.1, performance_p_danger=0.1, performance_label_index=0,
        scalability_label="LOW", scalability_p_top=0.2, scalability_p_critical=0.1, scalability_p_danger=0.1, scalability_label_index=0,
        maintainability_label="LOW", maintainability_p_top=0.1, maintainability_p_critical=0.1, maintainability_p_danger=0.1, maintainability_label_index=0,
        availability_label="LOW", availability_p_top=0.2, availability_p_critical=0.1, availability_p_danger=0.1, availability_label_index=0,
        compliance_label="LOW", compliance_p_top=0.1, compliance_p_critical=0.1, compliance_p_danger=0.1, compliance_label_index=0
    )
    db.add(row2)
    
    row3 = MCPLLMAxisScores(
        server_id="server3", criteria_version="v1", scored_at=now,
        security_label="LOW", security_p_top=0.2, security_p_critical=0.1, security_p_danger=0.1, security_label_index=0,
        reliability_label="LOW", reliability_p_top=0.3, reliability_p_critical=0.1, reliability_p_danger=0.1, reliability_label_index=0,
        performance_label="LOW", performance_p_top=0.1, performance_p_critical=0.1, performance_p_danger=0.1, performance_label_index=0,
        scalability_label="LOW", scalability_p_top=0.2, scalability_p_critical=0.1, scalability_p_danger=0.1, scalability_label_index=0,
        maintainability_label="LOW", maintainability_p_top=0.1, maintainability_p_critical=0.1, maintainability_p_danger=0.1, maintainability_label_index=0,
        availability_label="LOW", availability_p_top=0.2, availability_p_critical=0.1, availability_p_danger=0.1, availability_label_index=0,
        compliance_label="LOW", compliance_p_top=0.1, compliance_p_critical=0.1, compliance_p_danger=0.1, compliance_label_index=0
    )
    db.add(row3)
    db.commit()
    
    result = client.get("/servers/server2/risk-tier").json()
    assert result["risk_tier"] == "HIGH_RISK_ISOLATED", f"Expected HIGH_RISK_ISOLATED, got {result['risk_tier']}"
    
    summary = client.get("/risk-tier/summary").json()
    assert summary["tiers"]["HIGH_RISK_ISOLATED"] == 1, f"Expected 1 HIGH_RISK_ISOLATED, got {summary['tiers']['HIGH_RISK_ISOLATED']}"
    assert summary["tiers"]["LOW_RISK"] == 2, f"Expected 2 LOW_RISK, got {summary['tiers']['LOW_RISK']}"
    assert summary["total"] == 3
    
    print("PASS")