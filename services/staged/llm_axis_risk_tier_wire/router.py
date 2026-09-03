from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="", tags=["llm_axis_risk_tier_wire"])


class RiskTierWire(BaseModel):
    server_id: str
    axis_name: str
    label: str
    p_critical: Optional[float]
    p_danger: Optional[float]
    p_top: Optional[float]
    risk_tier: str

    class Config:
        from_attributes = True


def _compute_risk_tier(p_critical: Optional[float], p_danger: Optional[float]) -> str:
    if p_critical is not None and p_critical >= 0.5:
        return "CRITICAL"
    if p_danger is not None and p_danger >= 0.5:
        return "HIGH"
    if p_critical is not None and p_critical >= 0.2:
        return "MEDIUM"
    return "LOW"


@router.get("/signal-scores", response_model=List[RiskTierWire])
def signal_scores_endpoint(
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    session: Session = Depends(get_session),
):
    query = select(McpLlmAxisScore)
    if server_id:
        query = query.where(McpLlmAxisScore.server_id == server_id)
    if axis_name:
        query = query.where(McpLlmAxisScore.axis_name == axis_name)
    query = query.order_by(McpLlmAxisScore.scored_at.desc()).limit(1000)
    scores = session.execute(query).scalars().all()
    result = []
    for score in scores:
        result.append(
            RiskTierWire(
                server_id=score.server_id,
                axis_name=score.axis_name,
                label=score.label,
                p_critical=score.p_critical,
                p_danger=score.p_danger,
                p_top=score.p_top,
                risk_tier=_compute_risk_tier(score.p_critical, score.p_danger),
            )
        )
    return result


@router.get("/risk-axis-summary")
def risk_axis_summary(session: Session = Depends(get_session)):
    query = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
    ).distinct()
    axes = session.execute(query).all()
    return {
        "axes": [{"axis_name": a.axis_name, "label": a.label} for a in axes],
        "total_axes": len(axes),
    }


@router.get("/risk-tiers", response_model=List[str])
def get_risk_tiers():
    return ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(app)

    resp = client.get("/risk-tiers")
    assert resp.status_code == 200, f"risk-tiers failed: {resp.text}"
    assert resp.json() == ["CRITICAL", "HIGH", "MEDIUM", "LOW"], resp.json()

    resp = client.get("/risk-axis-summary")
    assert resp.status_code == 200, f"risk-axis-summary failed: {resp.text}"
    assert "total_axes" in resp.json(), resp.json()

    resp = client.get("/signal-scores")
    assert resp.status_code == 200, f"signal-scores failed: {resp.text}"
    assert isinstance(resp.json(), list), resp.json()

    print("PASS")