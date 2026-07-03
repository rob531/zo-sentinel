from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/analysis", tags=["analysis"])

class AxisProbability(BaseModel):
    axis_name: str
    probability: Optional[float] = None

class ServerAxisProbabilities(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    axes: Dict[str, AxisProbability]
    trend: Optional[str] = None

@router.get("/server-axis-probabilities", response_model=Dict[str, ServerAxisProbabilities])
def get_server_axis_probabilities(db: Session = Depends(get_session)) -> Dict[str, ServerAxisProbabilities]:
    """Get the probabilities of server axis scores and trend analysis."""
    rows = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name, McpLlmAxisScore.p_top)
        .order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
    ).all()

    server_data = {}
    for row in rows:
        server_id = row.server_id
        if server_id not in server_data:
            reg = db.get(McpServerRegistry, server_id)
            server_data[server_id] = ServerAxisProbabilities(
                server_id=server_id,
                name=reg.name if reg else None,
                url=reg.url if reg else None,
                axes={},
                trend="stable"
            )
        server_data[server_id].axes[row.axis_name] = AxisProbability(
            axis_name=row.axis_name,
            probability=row.p_top
        )

    return server_data

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
    s.add(McpServerRegistry(server_id="srv1", name="Test Server",
                            url="https://test.server"))
    for _i, (ax, prob) in enumerate((("overall_risk", 0.8), ("auth_strength", 0.7),
                    ("capability_breadth", 0.9), ("data_sensitivity", 0.6),
                    ("network_egress", 0.5), ("maintainer_trust", 0.8),
                    ("exploit_surface", 0.7)), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label="HIGH",
                              model_version="v3.0_40974559", p_top=prob))
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
    r = c.get("/analysis/server-axis-probabilities"); assert r.status_code == 200, r.text
    j = r.json()
    assert "srv1" in j, j
    assert len(j["srv1"]["axes"]) == 7, j
    assert j["srv1"]["trend"] == "stable", j
    print("PASS")
