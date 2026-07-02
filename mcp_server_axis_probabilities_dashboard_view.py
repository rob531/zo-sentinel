# deps: fastapi, pydantic, sqlalchemy
"""FastAPI router exposing GET /server-axis-probabilities. Reads the axis probabilities for a server from mcp_llm_axis_scores via the app DB session, returns {axis: {label, p_top}} with a rule-override (a CRITICAL axis forces the tier). fastapi + pydantic only; Postgres-portable SQL; no network in the self-test."""

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_probabilities"])

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

class AxisProbability(BaseModel):
    label: Optional[str] = None
    p_top: Optional[float] = None

@router.get("/server-axis-probabilities/{server_id}", response_model=Dict[str, AxisProbability])
def get_server_axis_probabilities(server_id: str, db: Session = Depends(get_session)) -> Dict[str, AxisProbability]:
    """Get the axis probabilities for a server, with a rule-override (a CRITICAL axis forces the tier)."""
    rows = db.execute(
        select(McpLlmAxisScore).where(
            McpLlmAxisScore.server_id == server_id,
        )
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scores for server_id {server_id!r}")

    axes: Dict[str, AxisProbability] = {}
    for r in rows:
        axes[r.axis_name] = AxisProbability(label=r.label, p_top=r.p_top)

    # Apply rule-override: a CRITICAL axis forces the tier
    if any(r.label == "CRITICAL" for r in rows):
        for axis in axes:
            axes[axis].label = "CRITICAL"

    return axes

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
    for _i, (ax, lbl, p) in enumerate((("overall_risk", "HIGH", 0.8), ("auth_strength", "STRONG", 0.9),
                    ("capability_breadth", "BROAD", 0.7), ("data_sensitivity", "CRITICAL", 0.95),
                    ("network_egress", "EXTERNAL", 0.6), ("maintainer_trust", "ESTABLISHED", 0.85),
                    ("exploit_surface", "MODERATE", 0.75)), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl, p_top=p,
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
    r = c.get("/api/server-axis-probabilities/srv1"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 7, j
    assert j["data_sensitivity"]["label"] == "CRITICAL", j
    assert c.get("/api/server-axis-probabilities/nope").status_code == 404
    print("PASS")
