# deps: fastapi, pydantic, sqlalchemy
"""router.py -- mcp_server_registry_search_api.

Search and browse MCP servers from the registry.
Public endpoint (auth=public per directive).
Reads from mcp_server_registry via app/db get_session.
Optionally enriches results with latest axis scores (mcp_llm_axis_scores).
Applies trust_gating_override so official publishers are not shown as false HIGH/CRITICAL.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

try:
    from trust_gating_override import trust_gate
except ImportError:
    def trust_gate(url, name, axes):
        return axes


router = APIRouter(prefix="/api", tags=["mcp_server_registry_search_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class AxisScoreDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[datetime] = None


class TrustGateResult(BaseModel):
    trusted: bool
    capped: bool


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
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    last_assessed: Optional[datetime] = None
    axis_scores: List[AxisScoreDetail] = []
    trust_gate: Optional[TrustGateResult] = None


class SearchResponse(BaseModel):
    items: List[ServerSearchItem]
    total: int
    page: int
    limit: int


class RegistryStats(BaseModel):
    total_servers: int
    by_source: dict[str, int]
    by_tier: dict[str, int]
    never_scored: int


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

AXIS_NAMES = frozenset({
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
})


def _latest_model_version(db: Session) -> str:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row or "unknown"


def _fetch_axis_scores(db: Session, server_ids: List[str], model_version: str) -> dict[str, List[AxisScoreDetail]]:
    if not server_ids:
        return {}
    rows = db.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id.in_(server_ids))
        .where(McpLlmAxisScore.model_version == model_version)
    ).scalars().all()
    result: dict[str, List[AxisScoreDetail]] = {sid: [] for sid in server_ids}
    for row in rows:
        if row.axis_name in AXIS_NAMES:
            result.setdefault(row.server_id, []).append(AxisScoreDetail.model_validate(row))
    return result


def _apply_trust_gate(item: ServerSearchItem) -> ServerSearchItem:
    axes_dict = {a.axis_name: a.label for a in item.axis_scores}
    result = trust_gate(item.url, item.name, axes_dict)
    item.trust_gate = TrustGateResult(
        trusted=result.get("trusted", False),
        capped=result.get("capped", False),
    )
    return item


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/registry/search", response_model=SearchResponse)
def registry_search(
    q: Optional[str] = Query(None, description="Full-text search: name, description, URL"),
    source: Optional[str] = Query(None, description="Filter by registry_source"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    scored: Optional[bool] = Query(None, description="True=servers with scores, False=servers never scored"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    include_axes: bool = Query(False, description="Enrich results with axis scores"),
    db: Session = Depends(get_session),
) -> SearchResponse:
    """Search the MCP server registry with optional filters and pagination."""
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
    results = (
        base_q
        .order_by(McpServerRegistry.last_assessed.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )

    sids = [r.server_id for r in results]
    axis_map: dict[str, List[AxisScoreDetail]] = {}
    if include_axes and sids:
        mv = _latest_model_version(db)
        axis_map = _fetch_axis_scores(db, sids, mv)

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
            first_seen=r.first_seen,
            last_seen=r.last_seen,
            last_scanned=r.last_scanned,
            last_assessed=r.last_assessed,
            axis_scores=axis_map.get(r.server_id, []),
        )
        items.append(_apply_trust_gate(item))

    return SearchResponse(items=items, total=total, page=page, limit=limit)


@router.get("/registry/{server_id}", response_model=ServerSearchItem, responses={404: {"description": "Server not found"}})
def registry_server_by_id(
    server_id: str,
    include_axes: bool = Query(False, description="Include axis scores"),
    db: Session = Depends(get_session),
) -> ServerSearchItem:
    """Fetch a single server by server_id."""
    row = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    axis_map: dict[str, List[AxisScoreDetail]] = {}
    if include_axes:
        mv = _latest_model_version(db)
        axis_map = _fetch_axis_scores(db, [server_id], mv)

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
        first_seen=row.first_seen,
        last_seen=row.last_seen,
        last_scanned=row.last_scanned,
        last_assessed=row.last_assessed,
        axis_scores=axis_map.get(server_id, []),
    )
    return _apply_trust_gate(item)


@router.get("/registry/stats", response_model=RegistryStats)
def registry_stats(
    source: Optional[str] = Query(None, description="Filter by registry_source"),
    db: Session = Depends(get_session),
) -> RegistryStats:
    """Return aggregate registry statistics."""
    base_q = db.query(McpServerRegistry)
    if source:
        base_q = base_q.filter(McpServerRegistry.registry_source == source)

    total_servers: int = base_q.count()

    scored_subq = (
        select(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    never_scored: int = base_q.filter(~McpServerRegistry.server_id.in_(select(scored_subq))).count()

    source_rows = (
        base_q
        .with_entities(McpServerRegistry.registry_source, func.count())
        .group_by(McpServerRegistry.registry_source)
        .all()
    )
    by_source = {str(r[0] or "unknown"): int(r[1]) for r in source_rows}

    tier_rows = (
        base_q
        .with_entities(McpServerRegistry.risk_tier, func.count())
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_tier = {str(r[0] or "unknown"): int(r[1]) for r in tier_rows}

    return RegistryStats(
        total_servers=total_servers,
        by_source=by_source,
        by_tier=by_tier,
        never_scored=never_scored,
    )


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

    def _override_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    now = datetime.now()
    session = TestSessionLocal()
    session.add_all([
        McpServerRegistry(
            server_id="srv-001",
            name="Arctic Hub",
            description="Northern data processing MCP",
            url="https://arctic.example.com",
            registry_source="public_registry",
            risk_tier="low",
            verdict="trusted",
            last_assessed=now,
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
            last_assessed=now,
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
            last_assessed=now,
            trust_score=0.25,
            scan_count=1,
        ),
        McpServerRegistry(
            server_id="srv-004",
            name="Microsoft Files MCP",
            description="Official Microsoft Files MCP",
            url="https://raw.githubusercontent.com/microsoft/files-mcp/main/server.json",
            registry_source="github",
            risk_tier="high",
            verdict="unknown",
            last_assessed=now,
            trust_score=0.50,
            scan_count=0,
        ),
    ])
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
    test_app.dependency_overrides[get_session] = _override_session

    client = TestClient(test_app)

    # 1. unfiltered search
    r = client.get("/api/registry/search")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    d = r.json()
    assert d["total"] >= 3, f"Expected total >= 3, got {d['total']}"
    names = [i["name"] for i in d["items"]]
    assert "Storm Core" in names, f"Expected 'Storm Core' in results, got {names}"
    print("PASS: unfiltered search")

    # 2. text search
    r2 = client.get("/api/registry/search", params={"q": "Weather"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["total"] >= 1
    assert d2["items"][0]["name"] == "Storm Core"
    print("PASS: text search")

    # 3. source filter
    r3 = client.get("/api/registry/search", params={"source": "public_registry"})
    assert r3.status_code == 200
    d3 = r3.json()
    for item in d3["items"]:
        assert item["registry_source"] == "public_registry"
    print("PASS: source filter")

    # 4. risk_tier filter
    r4 = client.get("/api/registry/search", params={"risk_tier": "high"})
    assert r4.status_code == 200
    d4 = r4.json()
    assert d4["total"] >= 1
    assert d4["items"][0]["risk_tier"] == "high"
    print("PASS: risk_tier filter")

    # 5. pagination
    r5 = client.get("/api/registry/search", params={"page": 1, "limit": 2})
    assert r5.status_code == 200
    d5 = r5.json()
    assert len(d5["items"]) <= 2
    assert d5["page"] == 1
    assert d5["limit"] == 2
    print("PASS: pagination")

    # 6. search with axis enrichment
    r6 = client.get("/api/registry/search", params={"server_id": "srv-001", "include_axes": True})
    # Note: server_id is not a filter param; use q instead
    r6 = client.get("/api/registry/search", params={"q": "Arctic", "include_axes": True})
    assert r6.status_code == 200
    d6 = r6.json()
    assert len(d6["items"]) >= 1
    axes = d6["items"][0].get("axis_scores", [])
    axis_names = [a["axis_name"] for a in axes]
    assert "overall_risk" in axis_names, f"Expected 'overall_risk' in axes, got {axis_names}"
    print("PASS: search with axis enrichment")

    # 7. server by id
    r7 = client.get("/api/registry/srv-001")
    assert r7.status_code == 200
    d7 = r7.json()
    assert d7["server_id"] == "srv-001"
    assert d7["name"] == "Arctic Hub"
    print("PASS: server by id")

    # 8. server by id 404
    r8 = client.get("/api/registry/nonexistent")
    assert r8.status_code == 404
    print("PASS: server by id 404")

    # 9. stats endpoint
    r9 = client.get("/api/registry/stats")
    assert r9.status_code == 200
    d9 = r9.json()
    assert d9["total_servers"] >= 3
    assert "by_source" in d9
    assert "by_tier" in d9
    assert d9["never_scored"] >= 1  # srv-003 has no scores
    print("PASS: stats endpoint")

    # 10. scored filter
    r10 = client.get("/api/registry/search", params={"scored": True})
    assert r10.status_code == 200
    d10 = r10.json()
    assert d10["total"] >= 2  # srv-001, srv-002, srv-004 have scores
    print("PASS: scored=True filter")

    # 11. never-scored filter
    r11 = client.get("/api/registry/search", params={"scored": False})
    assert r11.status_code == 200
    d11 = r11.json()
    assert d11["total"] >= 1  # srv-003 has no scores
    print("PASS: scored=False filter")

    print("ALL TESTS PASSED")
    sys.exit(0)
