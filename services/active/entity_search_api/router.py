# deps: fastapi, pydantic, sqlalchemy
"""Entity Search API -- full-text and facet-filtered search of MCP servers.

GET /api/servers/search
GET /api/servers/stats
GET /api/servers/{server_id}

Auth: public.
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["entity_search_api"])


# --- Pydantic models ---------------------------------------------------------

class ServerSearchResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str = Field(..., description="Unique server identifier")
    name: str = Field(..., description="Server display name")
    registry_source: Optional[str] = Field(None, description="Source registry")
    url: Optional[str] = Field(None, description="Server URL")
    description: Optional[str] = Field(None, description="Server description")
    trust_score: Optional[float] = Field(None, description="Trust score (0-1)")
    risk_tier: Optional[str] = Field(None, description="Risk tier")
    verdict: Optional[str] = Field(None, description="Verdict label")
    confidence: Optional[float] = Field(None, description="Confidence score (0-1)")
    last_scanned: Optional[datetime] = Field(None, description="Last scan timestamp")
    scan_count: int = Field(0, description="Number of scans performed")


class SearchResponse(BaseModel):
    servers: List[ServerSearchResult] = Field(default_factory=list, description="Matching servers")
    total: int = Field(..., ge=0, description="Total count of matching servers")
    limit: int = Field(..., ge=1, description="Items per page")
    offset: int = Field(..., ge=0, description="Starting offset")
    scanned_at: Optional[str] = Field(None, description="ISO timestamp of response")


class ServerStatsResponse(BaseModel):
    total_servers: int = Field(..., ge=0, description="Total registered servers")
    by_risk_tier: dict = Field(default_factory=dict, description="Count per risk tier")
    by_source: dict = Field(default_factory=dict, description="Count per registry source")


# --- Helpers -----------------------------------------------------------------

def _build_base_query(
    session: Session,
    q: Optional[str] = None,
    registry_source: Optional[str] = None,
    risk_tier: Optional[str] = None,
    trust_score_min: Optional[float] = None,
    trust_score_max: Optional[float] = None,
):
    """Build filtered query on McpServerRegistry."""
    query = session.query(McpServerRegistry)

    if q:
        pattern = f"%{q}%"
        query = query.filter(
            or_(
                McpServerRegistry.name.ilike(pattern),
                McpServerRegistry.description.ilike(pattern),
                McpServerRegistry.url.ilike(pattern),
            )
        )

    if registry_source:
        query = query.filter(McpServerRegistry.registry_source == registry_source)

    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    if trust_score_min is not None:
        query = query.filter(McpServerRegistry.trust_score >= trust_score_min)

    if trust_score_max is not None:
        query = query.filter(McpServerRegistry.trust_score <= trust_score_max)

    return query


# --- Endpoints ---------------------------------------------------------------

# NOTE: /servers/stats MUST come before /servers/{server_id} to avoid
# FastAPI matching "stats" as a server_id path parameter.

@router.get(
    "/servers/search",
    response_model=SearchResponse,
    summary="Search MCP servers",
    responses={400: {"description": "Invalid query parameters"}},
)
def search_servers(
    q: Annotated[Optional[str], Query(description="Full-text search on name/description/url")] = None,
    registry_source: Annotated[Optional[str], Query(description="Filter by registry source")] = None,
    risk_tier: Annotated[Optional[str], Query(description="Filter by risk tier")] = None,
    trust_score_min: Annotated[Optional[float], Query(description="Minimum trust score")] = None,
    trust_score_max: Annotated[Optional[float], Query(description="Maximum trust score")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Max results")] = 20,
    offset: Annotated[int, Query(ge=0, description="Result offset")] = 0,
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Full-text and facet-filtered search of registered MCP servers."""
    query = _build_base_query(
        db, q, registry_source, risk_tier, trust_score_min, trust_score_max
    )

    total = query.count()

    query = query.order_by(McpServerRegistry.trust_score.desc().nullslast())
    rows = query.offset(offset).limit(limit).all()

    servers = []
    for server in rows:
        servers.append(
            ServerSearchResult(
                server_id=server.server_id,
                name=server.name,
                registry_source=server.registry_source,
                url=server.url,
                description=server.description,
                trust_score=server.trust_score,
                risk_tier=server.risk_tier,
                verdict=server.verdict,
                confidence=server.confidence,
                last_scanned=server.last_scanned,
                scan_count=server.scan_count or 0,
            )
        )

    return SearchResponse(
        servers=servers,
        total=total,
        limit=limit,
        offset=offset,
        scanned_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/servers/stats",
    response_model=ServerStatsResponse,
    summary="Get server registry statistics",
)
def get_server_stats(
    db: Session = Depends(get_session),
) -> ServerStatsResponse:
    """Return aggregate counts of servers by risk tier and registry source."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    tier_counts = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_risk_tier = {str(row[0]) or "unknown": row[1] for row in tier_counts}

    source_counts = (
        db.query(
            McpServerRegistry.registry_source,
            func.count(McpServerRegistry.server_id),
        )
        .group_by(McpServerRegistry.registry_source)
        .all()
    )
    by_source = {str(row[0]) or "unknown": row[1] for row in source_counts}

    return ServerStatsResponse(
        total_servers=total,
        by_risk_tier=by_risk_tier,
        by_source=by_source,
    )


@router.get(
    "/servers/{server_id}",
    response_model=ServerSearchResult,
    summary="Get server details by ID",
    responses={404: {"description": "Server not found"}},
)
def get_server(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerSearchResult:
    """Return a single server by server_id."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server {server_id} not found",
        )

    return ServerSearchResult(
        server_id=server.server_id,
        name=server.name,
        registry_source=server.registry_source,
        url=server.url,
        description=server.description,
        trust_score=server.trust_score,
        risk_tier=server.risk_tier,
        verdict=server.verdict,
        confidence=server.confidence,
        last_scanned=server.last_scanned,
        scan_count=server.scan_count or 0,
    )


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)

    def _override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.now(timezone.utc)

    with TestSession() as db:
        servers = [
            McpServerRegistry(
                server_id="srv-001",
                name="Safe Calculator",
                registry_source="npm",
                url="https://registry.npmjs.org/calc",
                description="A safe calculator package",
                trust_score=0.95,
                verdict="safe",
                confidence=0.9,
                scan_count=5,
                last_scanned=now,
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="srv-002",
                name="Network Scanner",
                registry_source="github",
                url="https://github.com/example/scanner",
                description="Network port scanner",
                trust_score=0.3,
                verdict="elevated",
                confidence=0.8,
                scan_count=3,
                last_scanned=now,
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="srv-003",
                name="Data Exfiltrator",
                registry_source="unknown",
                url="https://evil.example/payload",
                description="Malicious data exfiltration tool",
                trust_score=0.05,
                verdict="critical",
                confidence=0.95,
                scan_count=1,
                last_scanned=now,
                risk_tier="critical",
            ),
        ]
        db.add_all(servers)
        db.commit()

    client = TestClient(test_app)

    # Test 1: Basic search returns all servers
    resp = client.get("/api/servers/search")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "servers" in data
    assert "total" in data
    assert data["total"] == 3

    # Test 2: Full-text search
    resp = client.get("/api/servers/search?q=calculator")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["servers"][0]["name"] == "Safe Calculator"

    # Test 3: Filter by registry_source
    resp = client.get("/api/servers/search?registry_source=npm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1

    # Test 4: Filter by risk_tier
    resp = client.get("/api/servers/search?risk_tier=critical")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["servers"][0]["server_id"] == "srv-003"
    assert data["servers"][0]["risk_tier"] == "critical"

    # Test 5: Filter by trust_score_min
    resp = client.get("/api/servers/search?trust_score_min=0.5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["servers"][0]["trust_score"] >= 0.5

    # Test 6: Pagination
    resp = client.get("/api/servers/search?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["servers"]) == 2
    assert data["total"] == 3
    assert data["limit"] == 2
    assert data["offset"] == 0

    resp = client.get("/api/servers/search?limit=2&offset=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["servers"]) == 1

    # Test 7: Stats endpoint (before {server_id} to avoid path conflict)
    resp = client.get("/api/servers/stats")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["total_servers"] == 3

    # Test 8: Get single server
    resp = client.get("/api/servers/srv-002")
    assert resp.status_code == 200
    data = resp.json()
    assert data["server_id"] == "srv-002"
    assert data["risk_tier"] == "medium"

    # Test 9: Server not found
    resp = client.get("/api/servers/nonexistent")
    assert resp.status_code == 404

    print("PASS")
