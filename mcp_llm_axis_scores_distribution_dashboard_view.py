# deps: fastapi, pydantic, sqlalchemy
from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["dashboard"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

class AxisDistribution(BaseModel):
    axis_name: str
    distribution: Dict[str, int]
    override_tier: Optional[str] = None

@router.get("/llm-axis-scores-distribution", response_model=list[AxisDistribution])
def get_llm_axis_scores_distribution(db: Session = Depends(get_session)) -> list[AxisDistribution]:
    """Get the distribution of LLM axis scores from the mcp_llm_axis_scores table."""
    distributions = []
    
    for axis in AXES:
        rows = db.execute(
            select(McpLlmAxisScore.label, func.count())
            .where(McpLlmAxisScore.axis_name == axis)
            .group_by(McpLlmAxisScore.label)
        ).all()
        
        distribution = {label: count for label, count in rows}
        override_tier = "CRITICAL" if distribution.get("CRITICAL", 0) > 0 else None
        
        distributions.append(AxisDistribution(
            axis_name=axis,
            distribution=distribution,
            override_tier=override_tier
        ))
    
    return distributions

if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id=f"srv{_i}", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/llm-axis-scores-distribution"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 7, j
    assert j[3]["override_tier"] == "CRITICAL", j  # data_sensitivity has CRITICAL
    print("PASS")
