# deps: fastapi, pydantic, sqlalchemy
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

_repo_root = str(Path(__file__).resolve().parents[2])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_risk_tier_lookup"])


class ServerRiskTierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    verdict_reasoning: Optional[str] = None
    confidence: Optional[float] = None
    last_assessed: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    last_scanned: Optional[str] = None
    scan_count: Optional[int] = None
    meta: Optional[dict] = None


@router.get("/servers/{server_id}/risk-tier", response_model=ServerRiskTierResponse)
def get_server_risk_tier(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerRiskTierResponse:
    """Return the current risk tier and verdict for a server by server_id."""
    stmt = select(McpServerRegistry).where(McpServerRegistry.server_id == server_id)
    result = session.execute(stmt)
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return ServerRiskTierResponse(
        server_id=record.server_id,
        name=record.name,
        risk_tier=record.risk_tier,
        verdict=record.verdict,
        verdict_reasoning=record.verdict_reasoning,
        confidence=record.confidence,
        last_assessed=str(record.last_assessed) if record.last_assessed else None,
        first_seen=str(record.first_seen) if record.first_seen else None,
        last_seen=str(record.last_seen) if record.last_seen else None,
        last_scanned=str(record.last_scanned) if record.last_scanned else None,
        scan_count=record.scan_count,
        meta=record.meta,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    test_session = SessionLocal()
    test_session.add_all([
        McpServerRegistry(
            server_id="srv-001",
            name="prod-api-server",
            risk_tier="TRUSTED_GENERAL",
            verdict="clean",
            verdict_reasoning="All checks passed",
            confidence=0.95,
            last_assessed=datetime.utcnow(),
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            scan_count=5,
            meta={"region": "us-east-1"},
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="staging-server",
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="suspicious",
            verdict_reasoning="Elevated threat signals detected",
            confidence=0.65,
            last_assessed=datetime.utcnow(),
            first_seen=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            scan_count=3,
            meta={"region": "eu-west-1"},
        ),
    ])
    test_session.commit()

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    app.dependency_overrides[get_session] = override_get_session

    client = app.test_client()

    resp1 = client.get("/api/servers/srv-001/risk-tier")
    assert resp1.status_code == 200, f"srv-001: {resp1.status_code}"
    data1 = resp1.json()
    assert data1["server_id"] == "srv-001"
    assert data1["risk_tier"] == "TRUSTED_GENERAL"
    assert data1["name"] == "prod-api-server"
    assert data1["confidence"] == 0.95

    resp2 = client.get("/api/servers/srv-002/risk-tier")
    assert resp2.status_code == 200, f"srv-002: {resp2.status_code}"
    data2 = resp2.json()
    assert data2["server_id"] == "srv-002"
    assert data2["risk_tier"] == "HIGH_RISK_ISOLATED"
    assert data2["name"] == "staging-server"
    assert data2["confidence"] == 0.65

    resp3 = client.get("/api/servers/nonexistent/risk-tier")
    assert resp3.status_code == 404

    print("PASS")
