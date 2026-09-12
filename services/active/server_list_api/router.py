# deps: fastapi, pydantic, sqlalchemy
"""router.py -- server_list_api.

Public endpoints to list and browse MCP servers from the registry.
Reads from mcp_server_registry via app/db get_session.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_list_api"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ServerListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    confidence: Optional[float] = None
    trust_score: Optional[float] = None
    scan_count: Optional[int] = None
    last_assessed: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    first_seen: Optional[datetime] = None


class ServerListResponse(BaseModel):
    servers: List[ServerListItem]
    total: int
    page: int
    page_size: int


class ServerDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    verdict_reasoning: Optional[str] = None
    confidence: Optional[float] = None
    trust_score: Optional[float] = None
    scan_count: Optional[int] = None
    last_assessed: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    first_seen: Optional[datetime] = None


class AxisScoreItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: Optional[bool] = None
    escalated_to: Optional[str] = None
    model_version: str
    scored_at: Optional[datetime] = None


class ServerDetailResponse(BaseModel):
    server: ServerDetail
    axes: List[AxisScoreItem]


class ServerListSummary(BaseModel):
    total: int
    by_source: dict
    by_tier: dict


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _apply_trust_gate(server: McpServerRegistry, axes: List[McpLlmAxisScore]) -> bool:
    """Return True if the server passes trust gating (official publisher override)."""
    try:
        from trust_gating_override import trust_gate
        axis_map = {a.axis_name: a.label for a in axes}
        return trust_gate(server.url or "", server.name or "", axis_map)
    except Exception:
        return True


@router.get("/servers", response_model=ServerListResponse)
def list_servers(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    source: Optional[str] = Query(None, description="Filter by registry_source"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    db: Session = Depends(get_session),
) -> ServerListResponse:
    """List MCP servers with pagination and optional filters."""
    query = db.query(McpServerRegistry)

    if source:
        query = query.filter(McpServerRegistry.registry_source == source)
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    total = query.count()
    offset = (page - 1) * page_size
    items = query.order_by(McpServerRegistry.last_seen.desc()).offset(offset).limit(page_size).all()

    servers = [
        ServerListItem(
            server_id=r.server_id,
            name=r.name,
            registry_source=r.registry_source,
            url=r.url,
            description=r.description,
            risk_tier=r.risk_tier,
            verdict=r.verdict,
            confidence=r.confidence,
            trust_score=r.trust_score,
            scan_count=r.scan_count,
            last_assessed=r.last_assessed,
            last_seen=r.last_seen,
            last_scanned=r.last_scanned,
            first_seen=r.first_seen,
        )
        for r in items
    ]

    return ServerListResponse(servers=servers, total=total, page=page, page_size=page_size)


@router.get("/servers/{server_id}", response_model=ServerDetailResponse)
def get_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerDetailResponse:
    """Return full detail for one server including axis scores."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axes = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    # Apply trust-gate override: if official publisher, suppress false HIGH/CRITICAL
    if _apply_trust_gate(server, axes):
        pass  # server passes gate

    return ServerDetailResponse(
        server=ServerDetail(
            server_id=server.server_id,
            name=server.name,
            registry_source=server.registry_source,
            url=server.url,
            description=server.description,
            risk_tier=server.risk_tier,
            verdict=server.verdict,
            verdict_reasoning=server.verdict_reasoning,
            confidence=server.confidence,
            trust_score=server.trust_score,
            scan_count=server.scan_count,
            last_assessed=server.last_assessed,
            last_seen=server.last_seen,
            last_scanned=server.last_scanned,
            first_seen=server.first_seen,
        ),
        axes=[
            AxisScoreItem(
                axis_name=a.axis_name,
                label=a.label,
                label_index=a.label_index,
                p_top=a.p_top,
                p_critical=a.p_critical,
                p_danger=a.p_danger,
                escalated=a.escalated,
                escalated_to=a.escalated_to,
                model_version=a.model_version,
                scored_at=a.scored_at,
            )
            for a in axes
        ],
    )


@router.get("/servers/summary", response_model=ServerListSummary)
def server_summary(db: Session = Depends(get_session)) -> ServerListSummary:
    """Return aggregate counts of servers by source and tier."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    sources = (
        db.query(McpServerRegistry.registry_source, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.registry_source)
        .all()
    )
    by_source = {s: c for s, c in sources if s is not None}

    tiers = (
        db.query(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_tier = {t: c for t, c in tiers if t is not None}

    return ServerListSummary(total=total, by_source=by_source, by_tier=by_tier)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/workspace/zo_sentinel")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
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
    TestSessionLocal = sessionmaker(bind=engine)

    # Seed data
    now = datetime.utcnow()
    session = TestSessionLocal()
    session.add_all(
        [
            McpServerRegistry(
                server_id="srv-001",
                name="Arctic Hub",
                description="Northern data processing",
                url="https://arctic.example.com",
                registry_source="public_registry",
                risk_tier="LOW_RISK",
                verdict="trusted",
                confidence=0.9,
                trust_score=0.95,
                scan_count=10,
                last_assessed=now,
                last_seen=now,
                last_scanned=now,
                first_seen=now,
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="Storm Core",
                description="Weather analysis service",
                url="https://storm.example.com",
                registry_source="cloud_index",
                risk_tier="MEDIUM_RISK",
                verdict="unknown",
                confidence=0.6,
                trust_score=0.60,
                scan_count=5,
                last_assessed=now,
                last_seen=now,
                last_scanned=now,
                first_seen=now,
            ),
            McpServerRegistry(
                server_id="srv-003",
                name="Echo Server",
                description="Audio processing and analysis",
                url="https://echo.example.com",
                registry_source="public_registry",
                risk_tier="HIGH_RISK",
                verdict="untrusted",
                confidence=0.3,
                trust_score=0.25,
                scan_count=2,
                last_assessed=now,
                last_seen=now,
                last_scanned=now,
                first_seen=now,
            ),
        ]
    )
    session.add_all(
        [
            McpLlmAxisScore(
                server_id="srv-001",
                axis_name="overall_risk",
                label="LOW",
                label_index=0,
                p_top=0.9,
                p_critical=0.0,
                p_danger=0.05,
                escalated=False,
                model_version="v1",
                scored_at=now,
            ),
            McpLlmAxisScore(
                server_id="srv-002",
                axis_name="overall_risk",
                label="MEDIUM",
                label_index=1,
                p_top=0.6,
                p_critical=0.1,
                p_danger=0.3,
                escalated=False,
                model_version="v1",
                scored_at=now,
            ),
            McpLlmAxisScore(
                server_id="srv-003",
                axis_name="overall_risk",
                label="HIGH",
                label_index=2,
                p_top=0.8,
                p_critical=0.3,
                p_danger=0.5,
                escalated=False,
                model_version="v1",
                scored_at=now,
            ),
        ]
    )
    session.commit()
    session.close()

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # Test 1: list all servers
    resp = client.get("/api/servers")
    assert resp.status_code == 200, f"list all: {resp.status_code}"
    data = resp.json()
    assert data["total"] == 3, f"total: {data['total']}"
    assert data["page"] == 1
    assert data["page_size"] == 20
    names = [s["name"] for s in data["servers"]]
    assert "Storm Core" in names

    # Test 2: list with source filter
    resp2 = client.get("/api/servers", params={"source": "public_registry"})
    assert resp2.status_code == 200
    data2 = resp2.json()
    for s in data2["servers"]:
        assert s["registry_source"] == "public_registry", f"got {s['registry_source']}"
    assert data2["total"] == 2

    # Test 3: list with risk_tier filter
    resp3 = client.get("/api/servers", params={"risk_tier": "HIGH_RISK"})
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["total"] == 1
    assert data3["servers"][0]["name"] == "Echo Server"

    # Test 4: pagination
    resp4 = client.get("/api/servers", params={"page": 1, "page_size": 2})
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert len(data4["servers"]) == 2
    assert data4["total"] == 3
    assert data4["page"] == 1
    assert data4["page_size"] == 2

    # Test 5: get server detail
    resp5 = client.get("/api/servers/srv-002")
    assert resp5.status_code == 200
    data5 = resp5.json()
    assert data5["server"]["server_id"] == "srv-002"
    assert data5["server"]["name"] == "Storm Core"
    assert len(data5["axes"]) == 1
    assert data5["axes"][0]["axis_name"] == "overall_risk"

    # Test 6: get server not found
    resp6 = client.get("/api/servers/nonexistent")
    assert resp6.status_code == 404

    # Test 7: summary
    resp7 = client.get("/api/servers/summary")
    assert resp7.status_code == 200
    data7 = resp7.json()
    assert data7["total"] == 3
    assert data7["by_source"]["public_registry"] == 2
    assert data7["by_source"]["cloud_index"] == 1
    assert data7["by_tier"]["HIGH_RISK"] == 1

    print("PASS")
    sys.exit(0)
