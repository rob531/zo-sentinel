# deps: fastapi, pydantic, sqlalchemy
"""router.py -- server_search_api.

Search and browse MCP servers from the registry with axis-score enrichment.
Public endpoint (auth=public per directive).
Reads from mcp_server_registry + mcp_llm_axis_scores via app/db get_session.
Applies trust_gating_override so official publishers are not shown as false HIGH/CRITICAL.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

# Trust-gating: cap official publishers at MEDIUM
try:
    from trust_gating_override import trust_gate
except ImportError:
    # Fallback: no-op gate when trust module is unavailable
    def trust_gate(url, name, axes):
        return axes


router = APIRouter(prefix="/api", tags=["server_search_api"])


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------

class AxisScoreSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False


class ServerSearchItem(BaseModel):
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
    scan_count: int = 0
    last_scanned: Optional[datetime] = None
    last_assessed: Optional[datetime] = None
    axis_scores: List[AxisScoreSummary] = []


class SearchResponse(BaseModel):
    items: List[ServerSearchItem]
    total: int
    page: int
    limit: int


class AggregateStats(BaseModel):
    total_servers: int
    scored_servers: int
    never_scored: int
    by_tier: dict[str, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

AXIS_NAMES = frozenset({
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
})


def _fetch_axis_scores(db: Session, server_ids: List[str]) -> dict[str, List[AxisScoreSummary]]:
    """Batch-fetch latest axis scores for a list of server IDs."""
    if not server_ids:
        return {}

    # Get latest model_version
    latest_version_row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    model_version = latest_version_row or "unknown"

    rows = db.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id.in_(server_ids))
        .where(McpLlmAxisScore.model_version == model_version)
    ).scalars().all()

    result: dict[str, List[AxisScoreSummary]] = {sid: [] for sid in server_ids}
    for row in rows:
        if row.axis_name in AXIS_NAMES:
            result.setdefault(row.server_id, []).append(
                AxisScoreSummary(
                    axis_name=row.axis_name,
                    label=row.label,
                    p_top=row.p_top,
                    p_critical=row.p_critical,
                    p_danger=row.p_danger,
                    escalated=bool(row.escalated),
                )
            )
    return result


def _apply_trust_gating(item: ServerSearchItem) -> ServerSearchItem:
    """Cap official publishers at MEDIUM per trust_gating_override policy."""
    axes_dict = {a.axis_name: a.label for a in item.axis_scores}
    capped = trust_gate(item.url, item.name, axes_dict)
    for axis_name, label in capped.items():
        for score in item.axis_scores:
            if score.axis_name == axis_name:
                score.label = label
    return item


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/servers/search", response_model=SearchResponse)
def server_search(
    q: Optional[str] = Query(None, description="Full-text search: name, description, URL"),
    source: Optional[str] = Query(None, description="Filter by registry_source"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier (low/medium/high/critical)"),
    scored: Optional[bool] = Query(None, description="True=servers with scores, False=servers never scored"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Search the MCP server registry with optional filters and pagination."""
    # Sub-query: server_ids that appear in mcp_llm_axis_scores
    scored_subq = (
        select(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )

    base_q = db.query(McpServerRegistry)

    if scored is True:
        base_q = base_q.filter(McpServerRegistry.server_id.in_(select(scored_subq)))
    elif scored is False:
        base_q = base_q.filter(~McpServerRegistry.server_id.in_(select(scored_subq)))

    if q:
        term = f"%{q}%"
        base_q = base_q.filter(
            McpServerRegistry.name.ilike(term)
            | McpServerRegistry.description.ilike(term)
            | McpServerRegistry.url.ilike(term)
        )

    if source:
        base_q = base_q.filter(McpServerRegistry.registry_source == source)

    if risk_tier:
        base_q = base_q.filter(McpServerRegistry.risk_tier == risk_tier)

    total = base_q.count()

    offset = (page - 1) * limit
    results = base_q.order_by(McpServerRegistry.last_assessed.desc().nullslast()).offset(offset).limit(limit).all()

    # Batch-fetch axis scores
    sids = [r.server_id for r in results]
    axis_map = _fetch_axis_scores(db, sids)

    items: List[ServerSearchItem] = []
    for r in results:
        item = ServerSearchItem(
            server_id=r.server_id,
            name=r.name,
            registry_source=r.registry_source,
            url=r.url,
            description=r.description,
            risk_tier=r.risk_tier,
            verdict=r.verdict,
            verdict_reasoning=r.verdict_reasoning,
            confidence=r.confidence,
            trust_score=r.trust_score,
            scan_count=r.scan_count or 0,
            last_scanned=r.last_scanned,
            last_assessed=r.last_assessed,
            axis_scores=axis_map.get(r.server_id, []),
        )
        item = _apply_trust_gating(item)
        items.append(item)

    return SearchResponse(items=items, total=total, page=page, limit=limit)


@router.get("/servers/stats", response_model=AggregateStats)
def server_stats(
    source: Optional[str] = Query(None, description="Filter stats by registry_source"),
    db: Session = Depends(get_session),
) -> AggregateStats:
    """Return aggregate server statistics for the registry."""
    base_q = db.query(McpServerRegistry)
    if source:
        base_q = base_q.filter(McpServerRegistry.registry_source == source)

    total_servers: int = base_q.count()

    scored_subq = (
        select(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    scored_q = base_q.filter(McpServerRegistry.server_id.in_(select(scored_subq)))
    scored_servers: int = scored_q.count()
    never_scored: int = total_servers - scored_servers

    tier_rows = (
        base_q
        .with_entities(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_tier = {str(r[0] or "unknown"): int(r[1]) for r in tier_rows}

    return AggregateStats(
        total_servers=total_servers,
        scored_servers=scored_servers,
        never_scored=never_scored,
        by_tier=by_tier,
    )


@router.get("/servers/{server_id}", response_model=ServerSearchItem, responses={404: {"description": "Server not found"}})
def server_by_id(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerSearchItem:
    """Fetch a single server by server_id with its axis scores."""
    row = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axis_map = _fetch_axis_scores(db, [server_id])
    item = ServerSearchItem(
        server_id=row.server_id,
        name=row.name,
        registry_source=row.registry_source,
        url=row.url,
        description=row.description,
        risk_tier=row.risk_tier,
        verdict=row.verdict,
        verdict_reasoning=row.verdict_reasoning,
        confidence=row.confidence,
        trust_score=row.trust_score,
        scan_count=row.scan_count or 0,
        last_scanned=row.last_scanned,
        last_assessed=row.last_assessed,
        axis_scores=axis_map.get(server_id, []),
    )
    return _apply_trust_gating(item)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)

    def _override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed data
    session = TestSessionLocal()
    session.add_all([
        McpServerRegistry(
            server_id="srv-001",
            name="Arctic Hub",
            description="Northern data processing MCP server",
            url="https://arctic.example.com",
            registry_source="public_registry",
            risk_tier="low",
            verdict="trusted",
            last_assessed=datetime.now(),
            trust_score=0.95,
            scan_count=3,
        ),
        McpServerRegistry(
            server_id="srv-002",
            name="Storm Core",
            description="Weather analysis MCP server",
            url="https://storm.example.com",
            registry_source="cloud_index",
            risk_tier="medium",
            verdict="unknown",
            last_assessed=datetime.now(),
            trust_score=0.60,
            scan_count=2,
        ),
        McpServerRegistry(
            server_id="srv-003",
            name="Echo Server",
            description="Audio processing and analysis",
            url="https://echo.example.com",
            registry_source="public_registry",
            risk_tier="high",
            verdict="untrusted",
            last_assessed=datetime.now(),
            trust_score=0.25,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="srv-004",
            name="Microsoft Files MCP",
            description="Official Microsoft Files MCP server",
            url="https://raw.githubusercontent.com/microsoft/files-mcp/main/server.json",
            registry_source="github",
            risk_tier="high",
            verdict="unknown",
            last_assessed=datetime.now(),
            trust_score=0.50,
            scan_count=0,
        ),
    ])
    session.commit()

    # Seed axis scores for srv-001 and srv-002
    now = datetime.now()
    session.add_all([
        McpLlmAxisScore(
            server_id="srv-001", axis_name="overall_risk", label="LOW",
            p_top=0.85, p_critical=0.01, p_danger=0.05,
            escalated=False, model_version="v1", scored_at=now,
        ),
        McpLlmAxisScore(
            server_id="srv-001", axis_name="maintainer_trust", label="VERIFIED",
            p_top=0.90, p_critical=0.0, p_danger=0.02,
            escalated=False, model_version="v1", scored_at=now,
        ),
        McpLlmAxisScore(
            server_id="srv-002", axis_name="overall_risk", label="MEDIUM",
            p_top=0.55, p_critical=0.05, p_danger=0.20,
            escalated=False, model_version="v1", scored_at=now,
        ),
        McpLlmAxisScore(
            server_id="srv-004", axis_name="overall_risk", label="CRITICAL",
            p_top=0.15, p_critical=0.60, p_danger=0.20,
            escalated=True, model_version="v1", scored_at=now,
        ),
        McpLlmAxisScore(
            server_id="srv-004", axis_name="maintainer_trust", label="VERIFIED",
            p_top=0.95, p_critical=0.0, p_danger=0.01,
            escalated=False, model_version="v1", scored_at=now,
        ),
    ])
    session.commit()
    session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(test_app)

    # Test 1: unfiltered search
    resp = client.get("/api/servers/search")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["total"] >= 3, f"Expected total >= 3, got {data['total']}"
    names = [item["name"] for item in data["items"]]
    assert "Storm Core" in names, f"Expected 'Storm Core' in results, got {names}"
    print("PASS: unfiltered search")

    # Test 2: text search
    resp2 = client.get("/api/servers/search", params={"q": "Weather"})
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total"] >= 1
    assert data2["items"][0]["name"] == "Storm Core"
    print("PASS: text search")

    # Test 3: source filter
    resp3 = client.get("/api/servers/search", params={"source": "public_registry"})
    assert resp3.status_code == 200
    data3 = resp3.json()
    for item in data3["items"]:
        assert item["registry_source"] == "public_registry"
    print("PASS: source filter")

    # Test 4: risk_tier filter
    resp4 = client.get("/api/servers/search", params={"risk_tier": "high"})
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["total"] >= 1
    assert data4["items"][0]["risk_tier"] == "high"
    print("PASS: risk_tier filter")

    # Test 5: pagination
    resp5 = client.get("/api/servers/search", params={"page": 1, "limit": 2})
    assert resp5.status_code == 200
    data5 = resp5.json()
    assert len(data5["items"]) <= 2
    assert data5["page"] == 1
    assert data5["limit"] == 2
    print("PASS: pagination")

    # Test 6: server_by_id endpoint
    resp6 = client.get("/api/servers/srv-001")
    assert resp6.status_code == 200
    data6 = resp6.json()
    assert data6["server_id"] == "srv-001"
    assert data6["name"] == "Arctic Hub"
    assert len(data6["axis_scores"]) >= 1
    print("PASS: server_by_id")

    # Test 7: server_by_id 404
    resp7 = client.get("/api/servers/nonexistent")
    assert resp7.status_code == 404
    print("PASS: server_by_id 404")

    # Test 8: stats endpoint
    resp8 = client.get("/api/servers/stats")
    assert resp8.status_code == 200
    data8 = resp8.json()
    assert data8["total_servers"] >= 3
    assert data8["scored_servers"] >= 1
    assert data8["never_scored"] >= 0
    assert "by_tier" in data8
    print("PASS: stats endpoint")

    # Test 9: scored filter
    resp9 = client.get("/api/servers/search", params={"scored": True})
    assert resp9.status_code == 200
    data9 = resp9.json()
    assert data9["total"] >= 2  # srv-001, srv-002, srv-004 have scores
    print("PASS: scored=True filter")

    # Test 10: never-scored filter
    resp10 = client.get("/api/servers/search", params={"scored": False})
    assert resp10.status_code == 200
    data10 = resp10.json()
    assert data10["total"] >= 1  # srv-003 has no scores
    print("PASS: scored=False filter")

    # Test 11: axis scores included in response
    resp11 = client.get("/api/servers/srv-001")
    assert resp11.status_code == 200
    data11 = resp11.json()
    axis_names = [a["axis_name"] for a in data11["axis_scores"]]
    assert "overall_risk" in axis_names
    assert data11["axis_scores"][0]["label"] == "LOW"
    print("PASS: axis scores in response")

    print("ALL TESTS PASSED")
    sys.exit(0)
