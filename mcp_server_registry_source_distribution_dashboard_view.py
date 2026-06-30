# deps: fastapi pydantic sqlalchemy
from __future__ import annotations

import json
from typing import Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["dashboard"])

class SourceDistribution(BaseModel):
    source: str
    count: int

@router.get("/server-registry-source-distribution", response_model=list[SourceDistribution])
def get_server_registry_source_distribution(db: Session = Depends(get_session)) -> list[SourceDistribution]:
    """Get the distribution of server registry sources."""
    rows = db.execute(
        select(McpServerRegistry.registry_source, func.count()).group_by(
            McpServerRegistry.registry_source).order_by(func.count().desc()).limit(8)
    ).all()
    return [SourceDistribution(source=s or "unknown", count=c) for s, c in rows]

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
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit", registry_source="github"))
    s.add(McpServerRegistry(server_id="srv2", name="Microsoft MCP",
                            url="https://github.com/microsoft/agent-toolkit", registry_source="github"))
    s.add(McpServerRegistry(server_id="srv3", name="Google MCP",
                            url="https://github.com/google/agent-toolkit", registry_source="google"))
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
    r = c.get("/api/server-registry-source-distribution"); assert r.status_code == 200, r.text
    j = r.json()
    assert len(j) == 2, j
    assert j[0]["source"] == "github", j
    assert j[0]["count"] == 2, j
    assert j[1]["source"] == "google", j
    assert j[1]["count"] == 1, j
    print("PASS")
