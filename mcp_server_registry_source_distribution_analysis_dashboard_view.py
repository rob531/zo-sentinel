from __future__ import annotations

# deps: fastapi, pydantic, sqlalchemy

import json
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


class SourceDistribution(BaseModel):
    source: str
    risk_tier: str
    count: int


def _compute_source_distribution(db: Session) -> Dict[str, Dict[str, int]]: 
    """Live aggregate of server registry source distribution by risk tier."""
    dist = db.execute(
        select(
            McpServerRegistry.registry_source,
            McpLlmAxisScore.label,
            func.count()
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id
        )
        .where(
            McpLlmAxisScore.axis_name == "overall_risk"
        )
        .group_by(
            McpServerRegistry.registry_source,
            McpLlmAxisScore.label
        )
    ).all()
    
    result = {}
    for source, risk_tier, count in dist:
        if source not in result:
            result[source] = {}
        result[source][risk_tier] = count
    
    return result


@router.get("/registry-sources/distribution", response_model=Dict[str, Dict[str, int]])
def get_registry_source_distribution(db: Session = Depends(get_session)) -> Dict[str, Dict[str, int]]: 
    """Get the distribution of server registry sources by risk tier."""
    try:
        distribution = _compute_source_distribution(db)
        return distribution
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    s.add(McpServerRegistry(server_id="srv1", name="Test Server 1",
                            url="https://test1.com", registry_source="source1"))
    s.add(McpServerRegistry(server_id="srv2", name="Test Server 2",
                            url="https://test2.com", registry_source="source2"))
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk", label="HIGH",
                              model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="overall_risk", label="MEDIUM",
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
    r = c.get("/api/registry-sources/distribution"); assert r.status_code == 200, r.text
    j = r.json()
    assert "source1" in j, j
    assert "source2" in j, j
    assert j["source1"]["HIGH"] == 1, j
    assert j["source2"]["MEDIUM"] == 1, j
    print("PASS")
