# deps: fastapi, pydantic, sqlalchemy, PyJWT, passlib
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["high_risk_servers_api"])


class HighRiskServerOut(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: str
    last_seen: Optional[str]
    last_scanned: Optional[str]
    scan_count: Optional[int]
    url: Optional[str]
    description: Optional[str]
    registry_source: Optional[str] = None


class HighRiskServersResponse(BaseModel):
    servers: List[HighRiskServerOut]
    total: int
    page: int
    page_size: int


class HighRiskSummary(BaseModel):
    total_high_risk: int
    by_tier: dict


@router.get("/servers/high-risk", response_model=HighRiskServersResponse, name="high_risk_servers")
def get_high_risk_servers(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    tier: Optional[str] = Query(None, description="Filter by specific tier: HIGH_RISK_ISOLATED or KNOWN_THREAT"),
    db: Session = Depends(get_session),
) -> HighRiskServersResponse:
    """Return servers with HIGH_RISK_ISOLATED or KNOWN_THREAT risk tiers, paginated."""
    risk_vals = []
    if tier:
        risk_vals = [tier.upper()]
    else:
        risk_vals = ["HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
    
    base_q = db.query(McpServerRegistry).filter(McpServerRegistry.risk_tier.in_(risk_vals))
    total = base_q.count()
    items = (
        base_q.order_by(McpServerRegistry.last_seen.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    servers = [
        HighRiskServerOut(
            server_id=item.server_id,
            name=item.name,
            risk_tier=item.risk_tier,
            last_seen=str(item.last_seen) if item.last_seen else None,
            last_scanned=str(item.last_scanned) if item.last_scanned else None,
            scan_count=item.scan_count,
            url=item.url,
            description=item.description,
            registry_source=item.registry_source,
        )
        for item in items
    ]
    return HighRiskServersResponse(servers=servers, total=total, page=page, page_size=page_size)


@router.get("/servers/high-risk/summary", response_model=HighRiskSummary, name="high_risk_summary")
def get_high_risk_summary(db: Session = Depends(get_session)) -> HighRiskSummary:
    """Return aggregate counts of high-risk servers by tier."""
    risk_vals = ["HIGH_RISK_ISOLATED", "KNOWN_THREAT"]
    total = db.query(func.count(McpServerRegistry.server_id)).filter(
        McpServerRegistry.risk_tier.in_(risk_vals)
    ).scalar() or 0
    
    by_tier = {}
    for tier in risk_vals:
        count = db.query(func.count(McpServerRegistry.server_id)).filter(
            McpServerRegistry.risk_tier == tier
        ).scalar() or 0
        by_tier[tier] = count
    
    return HighRiskSummary(total_high_risk=total, by_tier=by_tier)


@router.get("/servers/high-risk/{server_id}", response_model=HighRiskServerOut, name="high_risk_server_detail")
def get_high_risk_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> HighRiskServerOut:
    """Return detail for a specific server if it has a high-risk tier."""
    item = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not item:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    if item.risk_tier not in ("HIGH_RISK_ISOLATED", "KNOWN_THREAT"):
        raise HTTPException(status_code=404, detail=f"Server {server_id} is not high-risk")
    
    return HighRiskServerOut(
        server_id=item.server_id,
        name=item.name,
        risk_tier=item.risk_tier,
        last_seen=str(item.last_seen) if item.last_seen else None,
        last_scanned=str(item.last_scanned) if item.last_scanned else None,
        scan_count=item.scan_count,
        url=item.url,
        description=item.description,
        registry_source=item.registry_source,
    )


if __name__ == "__main__":
    import datetime as dt
    import sys
    from pathlib import Path

    # Ensure project root is on the path so `app` imports resolve
    _root = Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = get_test_session

    with SessionLocal() as db:
        now = dt.datetime.utcnow()
        db.add_all([
            McpServerRegistry(
                server_id="srv1", name="HighRiskOne", risk_tier="HIGH_RISK_ISOLATED",
                last_seen=now, last_scanned=now, scan_count=5,
                url="https://example.com/1", description="high risk isolated server",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="srv2", name="KnownThreat", risk_tier="KNOWN_THREAT",
                last_seen=now, last_scanned=now, scan_count=3,
                url="https://example.com/2", description="known threat server",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="srv3", name="LowRisk", risk_tier="LOW_RISK",
                last_seen=now, last_scanned=now, scan_count=1,
                url="https://example.com/3", description="low risk server",
                registry_source="test",
            ),
        ])
        db.commit()

    client = TestClient(that_app)
    
    # Test list endpoint
    resp = client.get("/api/servers/high-risk?page=1&page_size=10")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()
    assert data["total"] == 2, f"total mismatch {data['total']}"
    assert len(data["servers"]) == 2, f"servers count {len(data['servers'])}"
    tiers = {s["risk_tier"] for s in data["servers"]}
    assert tiers == {"HIGH_RISK_ISOLATED", "KNOWN_THREAT"}, f"tiers {tiers}"
    
    # Test summary endpoint
    resp = client.get("/api/servers/high-risk/summary")
    assert resp.status_code == 200, f"summary status {resp.status_code}"
    summary = resp.json()
    assert summary["total_high_risk"] == 2, f"summary total {summary['total_high_risk']}"
    assert summary["by_tier"]["HIGH_RISK_ISOLATED"] == 1
    assert summary["by_tier"]["KNOWN_THREAT"] == 1
    
    # Test detail endpoint - high risk server
    resp = client.get("/api/servers/high-risk/srv1")
    assert resp.status_code == 200, f"detail status {resp.status_code}"
    detail = resp.json()
    assert detail["server_id"] == "srv1"
    assert detail["risk_tier"] == "HIGH_RISK_ISOLATED"
    
    # Test detail endpoint - non-high-risk server returns 404
    resp = client.get("/api/servers/high-risk/srv3")
    assert resp.status_code == 404, f"non-high-risk should 404, got {resp.status_code}"
    
    # Test 404 for unknown server
    resp = client.get("/api/servers/high-risk/nonexistent")
    assert resp.status_code == 404, f"unknown server should 404, got {resp.status_code}"
    
    print("PASS")
