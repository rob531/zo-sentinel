# deps: fastapi, pydantic, sqlalchemy, requests
"""Server risk tier overview API.
Provides GET /servers/risk_tier_overview which aggregates the published overall
risk tier across all MCP servers and returns the latest risk tier per server.
"""

from __future__ import annotations

from collections import Counter
from typing import Dict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["risk_tier_overview"])

class RiskTierOverview(BaseModel):
    total_servers: int
    tier_counts: Dict[str, int]
    latest_overall_risk: Dict[str, str]

def _latest_model_version(db: Session, server_id: str) -> str | None:
    """Return the most recent model_version for a given server_id."""
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None

@router.get("/servers/risk_tier_overview", response_model=RiskTierOverview)
def get_risk_tier_overview(db: Session = Depends(get_session)) -> RiskTierOverview:
    """Aggregate risk tier statistics across all MCP servers.
    Returns total server count, a mapping of tier -> count, and the latest published
    overall risk tier per server.
    """
    # Fetch all distinct server IDs from the registry
    server_ids = db.execute(select(McpServerRegistry.server_id)).scalars().all()
    tier_counter: Counter[str] = Counter()
    latest_risk: Dict[str, str] = {}
    for sid in server_ids:
        mv = _latest_model_version(db, sid)
        if not mv:
            continue
        # Get the raw overall risk label for the latest model version
        row = db.execute(
            select(McpLlmAxisScore.label)
            .where(
                and_(
                    McpLlmAxisScore.server_id == sid,
                    McpLlmAxisScore.axis_name == "overall_risk",
                    McpLlmAxisScore.model_version == mv,
                )
            )
        ).first()
        raw_label = row[0] if row else None
        if not raw_label:
            continue
        # Apply trust gating to obtain the published tier
        reg = db.get(McpServerRegistry, sid)
        name = reg.name if reg else None
        url = reg.url if reg else None
        gate = trust_gate(url, name, {"overall_risk": raw_label})
        published = gate.get("published_overall_risk") or raw_label
        tier = published.upper()
        tier_counter[tier] += 1
        latest_risk[sid] = tier
    total = sum(tier_counter.values())
    return RiskTierOverview(total_servers=total, tier_counts=dict(tier_counter), latest_overall_risk=latest_risk)

if __name__ == "__main__":  # CI-safe self-test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # In‑memory SQLite setup
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = SessionLocal()
    # Seed two servers with different risk tiers
    s.add(McpServerRegistry(server_id="srv1", name="Server One", url="https://example.com/1"))
    s.add(McpServerRegistry(server_id="srv2", name="Server Two", url="https://example.com/2"))
    # Scores for both servers (same model version)
    for i, (sid, label) in enumerate((("srv1", "HIGH"), ("srv2", "LOW")), start=1):
        s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name="overall_risk", label=label,
                              model_version="v3.0_40974559"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Monkey‑patch trust_gate to be identity for the test
    def _identity_gate(url, name, labels):
        return {"published_overall_risk": labels.get("overall_risk")}

    app.dependency_overrides[get_session] = _override_session
    globals()["trust_gate"] = _identity_gate

    client = TestClient(app)
    resp = client.get("/api/servers/risk_tier_overview")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total_servers"] == 2, data
    assert data["tier_counts"].get("HIGH") == 1, data
    assert data["tier_counts"].get("LOW") == 1, data
    assert data["latest_overall_risk"].get("srv1") == "HIGH", data
    assert data["latest_overall_risk"].get("srv2") == "LOW", data
    print("PASS")
