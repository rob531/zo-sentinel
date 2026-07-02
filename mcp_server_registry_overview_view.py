from __future__ import annotations

# deps: fastapi, pydantic, sqlalchemy

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/servers", tags=["overview"])

class OverviewStats(BaseModel):
    total_servers: int
    trusted_servers: int
    caution_servers: int
    high_risk_servers: int

@router.get("/overview", response_model=OverviewStats)
def get_overview_stats(db: Session = Depends(get_session)) -> OverviewStats:
    """Overview statistics for the MCP servers."""
    total_servers = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar() or 0
    trusted_servers = db.execute(select(func.count()).where(McpServerRegistry.verdict == "trusted")).scalar() or 0
    caution_servers = db.execute(select(func.count()).where(McpServerRegistry.verdict == "caution")).scalar() or 0
    high_risk_servers = db.execute(select(func.count()).where(McpServerRegistry.verdict == "high_risk")).scalar() or 0
    
    return OverviewStats(
        total_servers=total_servers,
        trusted_servers=trusted_servers,
        caution_servers=caution_servers,
        high_risk_servers=high_risk_servers,
    )

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
    s.add(McpServerRegistry(server_id="srv1", name="Server 1", url="https://example.com", verdict="trusted"))
    s.add(McpServerRegistry(server_id="srv2", name="Server 2", url="https://example.org", verdict="caution"))
    s.add(McpServerRegistry(server_id="srv3", name="Server 3", url="https://example.net", verdict="high_risk"))
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
    r = c.get("/servers/overview"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_servers"] == 3, j
    assert j["trusted_servers"] == 1, j
    assert j["caution_servers"] == 1, j
    assert j["high_risk_servers"] == 1, j
    print("PASS")
