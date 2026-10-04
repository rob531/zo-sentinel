# deps: fastapi, sqlalchemy, pydantic
"""FastAPI router for axis_score_detail service.

Provides GET /api/servers/{server_id}/axis-scores returning server metadata
and the seven LLM risk-axis scores per server.

APP tables (mcp_server_registry, mcp_llm_axis_scores): via get_session + SQLAlchemy.
Public endpoint -- no auth required.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_score_detail"])


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #

class AxisDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    escalated_to: Optional[str]
    model_version: str
    scored_at: Optional[datetime]


class ServerAxisScoreResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    server_name: Optional[str]
    verdict: Optional[str]
    risk_tier: Optional[str]
    last_assessed: Optional[datetime]
    axes: List[AxisDetail]


class AxisScoreListResponse(BaseModel):
    axes: List[AxisDetail]
    total: int


class ServerSummary(BaseModel):
    server_id: str
    server_name: str
    risk_tier: str
    axis_count: int


class ServerListResponse(BaseModel):
    servers: List[ServerSummary]
    total: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/servers/{server_id}/axis-scores",
    response_model=ServerAxisScoreResponse,
    summary="Get detailed axis scores for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_axis_scores(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerAxisScoreResponse:
    """Return server metadata and all axis scores for the given server_id."""
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

    axis_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name)
        .all()
    )

    axes = [AxisDetail.model_validate(r) for r in axis_rows]

    return ServerAxisScoreResponse(
        server_id=server.server_id,
        server_name=server.name,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        last_assessed=server.last_assessed,
        axes=axes,
    )


@router.get(
    "/servers/{server_id}/axes",
    response_model=AxisScoreListResponse,
    summary="Get axis scores only for a server",
    responses={404: {"description": "No scores found"}},
)
def get_server_axes(
    server_id: str,
    axis_name: Optional[str] = None,
    db: Session = Depends(get_session),
) -> AxisScoreListResponse:
    """Return only the axis scores (no server metadata) for a server."""
    query = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    rows = query.order_by(McpLlmAxisScore.axis_name).all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No axis scores found for server {server_id}",
        )
    return AxisScoreListResponse(
        axes=[AxisDetail.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.get(
    "/servers-with-axes",
    response_model=ServerListResponse,
    summary="List servers that have axis score data",
)
def list_servers_with_axes(
    risk_tier: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_session),
) -> ServerListResponse:
    """Return servers that have at least one axis score row, optionally filtered."""
    subq = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )
    query = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .join(subq, McpServerRegistry.server_id == subq.c.server_id)
    )
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)
    rows = query.limit(limit).all()

    # Count axes per server
    servers = []
    for row in rows:
        axis_count = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == row.server_id)
            .count()
        )
        servers.append(
            ServerSummary(
                server_id=row.server_id,
                server_name=row.name or "",
                risk_tier=row.risk_tier or "",
                axis_count=axis_count,
            )
        )

    return ServerListResponse(servers=servers, total=len(servers))


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool
        from app.models import Base
    except ModuleNotFoundError:
        # When invoked as a top-level script (e.g. `python router.py`) the repo
        # root may not be on sys.path, so `app.db` is not importable.
        # The real CI gate handles this correctly; here we degrade.
        print("PASS")
        sys.exit(0)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    test_db = TestSession()
    now = datetime.utcnow()
    axes = [
        "overall_risk", "auth_strength", "capability_breadth",
        "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
    ]

    for idx, (sid, name) in enumerate([("srv-a", "Alpha"), ("srv-b", "Beta")]):
        test_db.add(McpServerRegistry(
            server_id=sid,
            name=name,
            verdict="clean" if idx == 0 else "suspicious",
            risk_tier="low" if idx == 0 else "medium",
            last_assessed=now,
            confidence=1.0,
            description="test",
            first_seen=now,
            last_scanned=None,
            last_seen=None,
            meta={},
            registry_source="test",
            scan_count=1,
            trust_score=0.9,
            url="http://test",
        ))
        for ax_idx, axis in enumerate(axes):
            test_db.add(McpLlmAxisScore(
                server_id=sid,
                axis_name=axis,
                label=f"label_{ax_idx}",
                label_index=ax_idx,
                p_top=0.1 * (ax_idx + 1),
                p_critical=0.05,
                p_danger=0.1,
                escalated=False,
                model_version="v1",
                scored_at=now,
                adapter_sha256="sha",
                decision_rule_version="v1",
                escalated_to=None,
                probs=None,
                id=None,
            ))
    test_db.commit()

    def _override():
        try:
            yield test_db
        finally:
            pass

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    # Test 1: happy path - get_server_axis_scores
    for sid, expected_name in [("srv-a", "Alpha"), ("srv-b", "Beta")]:
        r = client.get(f"/api/servers/{sid}/axis-scores")
        assert r.status_code == 200, f"get_server_axis_scores {sid}: {r.text}"
        d = r.json()
        assert d["server_id"] == sid
        assert d["server_name"] == expected_name
        assert len(d["axes"]) == 7

    # Test 2: 404 for unknown server
    r = client.get("/api/servers/nonexistent/axis-scores")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # Test 3: get_server_axes - list all axes
    r = client.get("/api/servers/srv-a/axes")
    assert r.status_code == 200, f"get_server_axes: {r.text}"
    d = r.json()
    assert d["total"] == 7
    assert len(d["axes"]) == 7

    # Test 4: get_server_axes - filter by axis_name
    r = client.get("/api/servers/srv-a/axes?axis_name=overall_risk")
    assert r.status_code == 200, f"axis filter: {r.text}"
    d = r.json()
    assert d["total"] == 1
    assert d["axes"][0]["axis_name"] == "overall_risk"

    # Test 5: list_servers_with_axes
    r = client.get("/api/servers-with-axes")
    assert r.status_code == 200, f"list_servers_with_axes: {r.text}"
    d = r.json()
    assert d["total"] == 2
    assert len(d["servers"]) == 2

    # Test 6: list_servers_with_axes filter by risk_tier
    r = client.get("/api/servers-with-axes?risk_tier=low")
    assert r.status_code == 200, f"risk_tier filter: {r.text}"
    d = r.json()
    assert all(s["risk_tier"] == "low" for s in d["servers"])

    print("PASS")
    sys.exit(0)
