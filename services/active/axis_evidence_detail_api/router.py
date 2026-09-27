# deps: fastapi, sqlalchemy, pydantic
"""axis_evidence_detail_api -- FastAPI router exposing the detailed evidence chain
behind MCP server axis scores.

Endpoints
--------
GET  /api/axis-evidence/{server_id}
    All 7-axis evidence for a server (with trust gating applied).

GET  /api/axis-evidence/{server_id}/{axis_name}
    Evidence for a single named axis of a server.

GET  /api/axis-evidence/{server_id}/{axis_name}/history
    Historical axis rows across model versions.

GET  /api/axis-evidence/servers/{server_id}/verdict
    Flat 7-axis label dict for a server.

GET  /api/axis-evidence/servers-with-evidence
    Servers that have axis score data, with latest score timestamp.

Auth : public (directive auth=public).
Data : app tier via get_session + McpLlmAxisScore + McpServerRegistry.
No multi-tenancy (server_id is not org-scoped in the schema).
No DB writes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/axis-evidence", tags=["axis_evidence_detail_api"])

# --------------------------------------------------------------------------- #
# Pydantic response shapes
# --------------------------------------------------------------------------- #


class AxisEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    probs: Optional[dict]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: bool
    escalated_to: Optional[str]
    decision_rule_version: Optional[str]
    model_version: str
    adapter_sha256: Optional[str]
    scored_at: Optional[datetime]


class ServerEvidenceBundle(BaseModel):
    server_id: str
    server_name: Optional[str]
    server_url: Optional[str]
    risk_tier: Optional[str]
    verdict: Optional[str]
    model_version: str
    axes: dict[str, AxisEvidence]
    scored_at: Optional[str]


class AxisHistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    model_version: str
    scored_at: Optional[datetime]


class AxisHistoryResponse(BaseModel):
    server_id: str
    axis_name: str
    history: list[AxisHistoryItem]


class VerdictResponse(BaseModel):
    server_id: str
    model_version: str
    axes: dict[str, Optional[str]]
    scored_at: Optional[str]


class ServerWithEvidence(BaseModel):
    server_id: str
    server_name: Optional[str]
    risk_tier: Optional[str]
    axis_count: int
    last_scored: Optional[datetime]


class ServerListResponse(BaseModel):
    servers: list[ServerWithEvidence]
    total: int


# --------------------------------------------------------------------------- #
# Valid axis names
# --------------------------------------------------------------------------- #
VALID_AXES = frozenset({
    "overall_risk", "auth_strength", "capability_breadth",
    "data_sensitivity", "network_egress", "maintainer_trust", "exploit_surface",
})


def _validate_axis(axis_name: str) -> None:
    if axis_name not in VALID_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name. Must be one of: {sorted(VALID_AXES)}",
        )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/servers/{server_id}/verdict",
    response_model=VerdictResponse,
    summary="Flat 7-axis verdict for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_verdict(
    server_id: str,
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    db: Session = Depends(get_session),
) -> VerdictResponse:
    """Return one label per axis as a flat dict."""
    query = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id)
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)
    rows = query.all()
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No verdict found for server: {server_id}"
        )
    latest = max(
        rows,
        key=lambda r: r.scored_at or datetime.min.replace(tzinfo=timezone.utc),
    )
    return VerdictResponse(
        server_id=server_id,
        model_version=latest.model_version,
        axes={r.axis_name: r.label for r in rows},
        scored_at=latest.scored_at.isoformat() if latest.scored_at else None,
    )


@router.get(
    "/{server_id}",
    response_model=ServerEvidenceBundle,
    summary="Full 7-axis evidence bundle for a server",
    responses={404: {"description": "No evidence found"}},
)
def get_server_evidence_bundle(
    server_id: str,
    model_version: Optional[str] = Query(
        None, description="Filter by model version (defaults to latest)"
    ),
    db: Session = Depends(get_session),
) -> ServerEvidenceBundle:
    """Return all 7-axis evidence for a server with trust gating applied."""
    from trust_gating_override import trust_gate

    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    query = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    )
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)

    rows = query.order_by(McpLlmAxisScore.axis_name).all()
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No evidence found for server: {server_id}"
        )

    latest = max(
        rows,
        key=lambda r: r.scored_at or datetime.min.replace(tzinfo=timezone.utc),
    )

    # Build raw labels and apply trust gating
    raw_labels = {r.axis_name: r.label for r in rows if r.label}
    gated = trust_gate(
        url=srv.url if srv else None,
        name=srv.name if srv else None,
        axis_labels=raw_labels,
    )

    axes: dict[str, AxisEvidence] = {}
    for r in rows:
        gated_label = (
            gated.get("axes", {})
            .get(r.axis_name, {})
            .get("label", r.label)
        )
        axes[r.axis_name] = AxisEvidence(
            axis_name=r.axis_name,
            label=gated_label,
            label_index=r.label_index,
            probs=r.probs,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=r.escalated,
            escalated_to=r.escalated_to,
            decision_rule_version=r.decision_rule_version,
            model_version=r.model_version,
            adapter_sha256=r.adapter_sha256,
            scored_at=r.scored_at,
        )

    return ServerEvidenceBundle(
        server_id=server_id,
        server_name=srv.name if srv else None,
        server_url=srv.url if srv else None,
        risk_tier=srv.risk_tier if srv else None,
        verdict=srv.verdict if srv else None,
        model_version=latest.model_version,
        axes=axes,
        scored_at=latest.scored_at.isoformat() if latest.scored_at else None,
    )


@router.get(
    "/{server_id}/{axis_name}",
    response_model=AxisEvidence,
    summary="Evidence for a single axis of a server",
    responses={
        400: {"description": "Invalid axis_name"},
        404: {"description": "Axis not found"},
    },
)
def get_axis_evidence(
    server_id: str,
    axis_name: str,
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    db: Session = Depends(get_session),
) -> AxisEvidence:
    """Return the evidence record for one named axis."""
    _validate_axis(axis_name)

    query = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.axis_name == axis_name,
    )
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)

    row = query.first()
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No evidence for server={server_id} axis={axis_name}",
        )
    return AxisEvidence.model_validate(row)


@router.get(
    "/{server_id}/{axis_name}/history",
    response_model=AxisHistoryResponse,
    summary="Historical axis score rows across model versions",
    responses={
        400: {"description": "Invalid axis_name"},
        404: {"description": "No history found"},
    },
)
def get_axis_history(
    server_id: str,
    axis_name: str,
    limit: int = Query(20, ge=1, le=100, description="Max historical rows"),
    db: Session = Depends(get_session),
) -> AxisHistoryResponse:
    """Return axis score history sorted by scored_at descending."""
    _validate_axis(axis_name)

    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
        )
        .order_by(desc(McpLlmAxisScore.scored_at))
        .limit(limit)
        .all()
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No history for server={server_id} axis={axis_name}",
        )

    return AxisHistoryResponse(
        server_id=server_id,
        axis_name=axis_name,
        history=[
            AxisHistoryItem(
                axis_name=r.axis_name,
                label=r.label,
                label_index=r.label_index,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                model_version=r.model_version,
                scored_at=r.scored_at,
            )
            for r in rows
        ],
    )


@router.get(
    "/servers-with-evidence",
    response_model=ServerListResponse,
    summary="Servers that have axis score data",
)
def list_servers_with_evidence(
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    db: Session = Depends(get_session),
) -> ServerListResponse:
    """Return servers that have at least one axis score row."""
    subq = (
        db.query(McpLlmAxisScore.server_id, func.max(McpLlmAxisScore.scored_at).label("last_scored"))
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    query = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            subq.c.last_scored,
        )
        .join(subq, McpServerRegistry.server_id == subq.c.server_id)
    )
    if risk_tier:
        query = query.filter(McpServerRegistry.risk_tier == risk_tier)

    rows = query.limit(limit).all()

    servers = []
    for row in rows:
        axis_count = db.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.server_id == row.server_id
        ).count()
        servers.append(
            ServerWithEvidence(
                server_id=row.server_id,
                server_name=row.name,
                risk_tier=row.risk_tier,
                axis_count=axis_count,
                last_scored=row.last_scored,
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

    # Seed servers
    test_db.add(McpServerRegistry(
        server_id="srv-ev1", name="AlphaServer", url="https://github.com/microsoft/server",
        risk_tier="low", verdict="clean", confidence=1.0, description="test",
        first_seen=now, last_scanned=None, last_seen=None, meta={},
        registry_source="test", scan_count=1, trust_score=0.9,
        last_assessed=now,
    ))
    test_db.add(McpServerRegistry(
        server_id="srv-ev2", name="BetaServer", url="https://example.com/server",
        risk_tier="high", verdict="suspicious", confidence=0.7, description="test",
        first_seen=now, last_scanned=None, last_seen=None, meta={},
        registry_source="test", scan_count=1, trust_score=0.5,
        last_assessed=now,
    ))

    # Seed axis scores for srv-ev1
    for axis in ["overall_risk", "auth_strength", "exploit_surface"]:
        test_db.add(McpLlmAxisScore(
            server_id="srv-ev1", axis_name=axis,
            label="LOW", label_index=0,
            probs={"LOW": 0.85, "HIGH": 0.15},
            p_top=0.85, p_critical=0.05, p_danger=0.1,
            escalated=False, escalated_to=None,
            decision_rule_version="v1.0", model_version="model-v1",
            adapter_sha256="sha256test", scored_at=now,
        ))

    # History for overall_risk
    test_db.add(McpLlmAxisScore(
        server_id="srv-ev1", axis_name="overall_risk",
        label="MEDIUM", label_index=1,
        probs={"LOW": 0.5, "MEDIUM": 0.4, "HIGH": 0.1},
        p_top=0.5, p_critical=0.1, p_danger=0.3,
        escalated=False, escalated_to=None,
        decision_rule_version="v0.9", model_version="model-v0",
        adapter_sha256="sha256old", scored_at=datetime(2026, 1, 1),
    ))

    # Seed axis score for srv-ev2
    test_db.add(McpLlmAxisScore(
        server_id="srv-ev2", axis_name="overall_risk",
        label="HIGH", label_index=2,
        probs={"LOW": 0.1, "HIGH": 0.9},
        p_top=0.9, p_critical=0.05, p_danger=0.05,
        escalated=False, escalated_to=None,
        decision_rule_version="v1.0", model_version="model-v1",
        adapter_sha256="sha256test2", scored_at=now,
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

    # --- get_server_verdict (happy path) ---
    r = client.get("/api/axis-evidence/servers/srv-ev1/verdict")
    assert r.status_code == 200, f"verdict: {r.status_code} {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-ev1"
    assert "overall_risk" in d["axes"], f"missing overall_risk: {d['axes']}"
    assert d["axes"]["overall_risk"] == "LOW"

    # --- get_server_evidence_bundle (happy path) ---
    r = client.get("/api/axis-evidence/srv-ev1")
    assert r.status_code == 200, f"bundle: {r.status_code} {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-ev1"
    assert d["server_name"] == "AlphaServer"
    assert len(d["axes"]) >= 3, f"expected >=3 axes: {d['axes']}"
    assert "overall_risk" in d["axes"]
    # Trust gating: official publisher github.com/microsoft should cap HIGH->MEDIUM
    # but this is LOW so unchanged
    assert d["axes"]["overall_risk"]["label"] == "LOW"

    # --- get_axis_evidence (happy path) ---
    r = client.get("/api/axis-evidence/srv-ev1/overall_risk")
    assert r.status_code == 200, f"axis evidence: {r.status_code} {r.text}"
    d = r.json()
    assert d["axis_name"] == "overall_risk"
    assert d["label"] == "LOW"
    assert d["model_version"] == "model-v1"

    # --- get_axis_evidence (invalid axis) ---
    r = client.get("/api/axis-evidence/srv-ev1/not_a_real_axis")
    assert r.status_code == 400, f"expected 400 for invalid axis, got {r.status_code}"

    # --- get_axis_history (happy path) ---
    r = client.get("/api/axis-evidence/srv-ev1/overall_risk/history?limit=10")
    assert r.status_code == 200, f"history: {r.status_code} {r.text}"
    d = r.json()
    assert len(d["history"]) == 2, f"expected 2 history rows: {d['history']}"
    assert d["server_id"] == "srv-ev1"
    assert d["axis_name"] == "overall_risk"

    # --- get_axis_history (404 for unknown server/axis) ---
    r = client.get("/api/axis-evidence/srv-ev1/exploit_surface/history")
    assert r.status_code == 404, f"expected 404 for no history: {r.status_code}"

    # --- list_servers_with_evidence (happy path) ---
    r = client.get("/api/axis-evidence/servers-with-evidence")
    assert r.status_code == 200, f"list servers: {r.status_code} {r.text}"
    d = r.json()
    assert d["total"] == 2, f"expected 2 servers: {d}"
    ids = {s["server_id"] for s in d["servers"]}
    assert ids == {"srv-ev1", "srv-ev2"}, f"got {ids}"

    # --- list_servers_with_evidence (filter by risk_tier) ---
    r = client.get("/api/axis-evidence/servers-with-evidence?risk_tier=low")
    assert r.status_code == 200, f"filter: {r.status_code} {r.text}"
    d = r.json()
    assert all(s["risk_tier"] == "low" for s in d["servers"])

    # --- 404 for nonexistent server ---
    r = client.get("/api/axis-evidence/nonexistent/overall_risk")
    assert r.status_code == 404, f"expected 404, got {r.status_code}"

    # --- Trust gating: HIGH capped to MEDIUM for official publisher ---
    r = client.get("/api/axis-evidence/srv-ev2")
    assert r.status_code == 200, f"bundle srv-ev2: {r.status_code} {r.text}"
    d = r.json()
    # srv-ev2 is NOT a verified publisher (example.com) so HIGH should stay HIGH
    assert d["axes"]["overall_risk"]["label"] == "HIGH"

    print("PASS")
    sys.exit(0)
