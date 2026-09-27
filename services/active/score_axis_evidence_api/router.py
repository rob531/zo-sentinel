# deps: fastapi, sqlalchemy, pydantic
"""score_axis_evidence_api -- FastAPI router exposing the evidence chain behind
MCP server axis scores.

GET /api/score-axis-evidence/{server_id}
    Return the 7-axis evidence bundle for a server (all axes, all metadata).

GET /api/score-axis-evidence/{server_id}/{axis_name}
    Return the evidence for a single axis of a server.

GET /api/score-axis-evidence/{server_id}/{axis_name}/history
    Return historical axis score rows for a server+axis across model versions.

GET /api/score-axis-evidence/servers/{server_id}/verdict
    Return the flat 7-axis verdict for a server.

GET /api/score-axis-evidence/scores
    List axis scores with optional filters and pagination.

Auth: public (directive auth=public).
Data: app tier via get_session + McpLlmAxisScore + McpServerRegistry.
No multi-tenancy required (server_id is not org-scoped in the schema).
No DB writes.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc
from sqlalchemy.orm import Session

# Set repo root so app.* imports resolve correctly
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/score-axis-evidence", tags=["score_axis_evidence_api"])

# --------------------------------------------------------------------------- #
# Pydantic response shapes
# --------------------------------------------------------------------------- #


class AxisEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    axis_name: str
    label: Optional[str]
    label_index: Optional[int]
    probs: Optional[dict]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]
    escalated_to: Optional[str]
    decision_rule_version: Optional[str]
    model_version: str
    adapter_sha256: Optional[str]
    scored_at: Optional[datetime]


class ServerEvidenceBundle(BaseModel):
    server_id: str
    server_name: Optional[str]
    server_url: Optional[str]
    model_version: str
    axes: dict[str, AxisEvidenceResponse]
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


class AxisListResponse(BaseModel):
    scores: list[AxisEvidenceResponse]
    total: int


# --------------------------------------------------------------------------- #
# Valid axis names
# --------------------------------------------------------------------------- #
VALID_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


def _validate_axis(axis_name: str) -> None:
    if axis_name not in VALID_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name. Must be one of: {sorted(VALID_AXES)}",
        )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/{server_id}", response_model=ServerEvidenceBundle)
def get_server_evidence_bundle(
    server_id: str,
    model_version: Optional[str] = Query(
        None, description="Filter by model version (defaults to latest)"
    ),
    db: Session = Depends(get_session),
) -> ServerEvidenceBundle:
    """
    Return all 7-axis evidence for a server.

    Joins McpLlmAxisScore (scores) with McpServerRegistry (server metadata).
    Applies trust_gating_override so official publishers show accurate verdicts.
    """
    from trust_gating_override import trust_gate

    # Fetch server metadata
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    # Fetch all axis rows for this server
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

    # Determine the effective model version (latest by scored_at)
    latest_row = max(
        rows, key=lambda r: r.scored_at or datetime.min.replace(tzinfo=timezone.utc)
    )
    effective_model_version = latest_row.model_version

    # Build the raw axis labels dict for trust gating
    raw_labels = {r.axis_name: r.label for r in rows if r.label}

    # Apply trust gating so official publishers aren't shown as false HIGH/CRITICAL
    gated = trust_gate(
        url=srv.url if srv else None,
        name=srv.name if srv else None,
        axis_labels=raw_labels,
    )

    # Rebuild axis responses using gated labels where trust_gate modified them
    axes: dict[str, AxisEvidenceResponse] = {}
    for r in rows:
        gated_label = (
            gated.get("axes", {})
            .get(r.axis_name, {})
            .get("label", r.label)
        )
        axes[r.axis_name] = AxisEvidenceResponse(
            server_id=r.server_id,
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
        model_version=effective_model_version,
        axes=axes,
        scored_at=(
            latest_row.scored_at.isoformat() if latest_row.scored_at else None
        ),
    )


@router.get("/{server_id}/{axis_name}", response_model=AxisEvidenceResponse)
def get_axis_evidence(
    server_id: str,
    axis_name: str,
    model_version: Optional[str] = Query(
        None, description="Filter by model version"
    ),
    db: Session = Depends(get_session),
) -> AxisEvidenceResponse:
    """Return evidence for a single axis of a server."""
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

    return AxisEvidenceResponse.model_validate(row)


@router.get(
    "/{server_id}/{axis_name}/history",
    response_model=AxisHistoryResponse,
)
def get_axis_history(
    server_id: str,
    axis_name: str,
    limit: int = Query(20, ge=1, le=100, description="Max historical rows"),
    db: Session = Depends(get_session),
) -> AxisHistoryResponse:
    """Return historical axis score rows across model versions."""
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
    "/servers/{server_id}/verdict",
    response_model=dict,
)
def get_server_verdict(
    server_id: str,
    model_version: Optional[str] = Query(
        None, description="Filter by model version"
    ),
    db: Session = Depends(get_session),
) -> dict:
    """
    Return the flat 7-axis verdict for a server (one label per axis).
    Alias for the verdict-watching use case.
    """
    query = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    )
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)

    rows = query.all()
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"No verdict found for server: {server_id}"
        )

    return {
        "server_id": server_id,
        "model_version": rows[0].model_version,
        "axes": {r.axis_name: r.label for r in rows},
        "scored_at": (
            rows[0].scored_at.isoformat() if rows[0].scored_at else None
        ),
    }


@router.get("/scores", response_model=AxisListResponse)
def list_scores(
    server_id: Optional[str] = Query(None, description="Filter by server_id"),
    axis_name: Optional[str] = Query(None, description="Filter by axis_name"),
    model_version: Optional[str] = Query(None, description="Filter by model version"),
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_session),
) -> AxisListResponse:
    """List axis scores with optional filters and pagination."""
    if axis_name:
        _validate_axis(axis_name)

    query = db.query(McpLlmAxisScore)
    if server_id:
        query = query.filter(McpLlmAxisScore.server_id == server_id)
    if axis_name:
        query = query.filter(McpLlmAxisScore.axis_name == axis_name)
    if model_version:
        query = query.filter(McpLlmAxisScore.model_version == model_version)

    total = query.count()
    rows = (
        query.order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AxisListResponse(
        scores=[AxisEvidenceResponse.model_validate(r) for r in rows],
        total=total,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
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
    TestSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    _test_db = TestSession()

    now = datetime.utcnow()

    # Seed server registry
    _test_db.add(
        McpServerRegistry(
            server_id="srv-ev-1",
            name="TestServer",
            url="https://github.com/test/server",
            risk_tier="medium",
        )
    )

    # Seed axis scores
    for axis in ["overall_risk", "auth_strength"]:
        _test_db.add(
            McpLlmAxisScore(
                server_id="srv-ev-1",
                axis_name=axis,
                label="LOW",
                label_index=0,
                probs={"LOW": 0.9, "HIGH": 0.1},
                p_top=0.9,
                p_critical=0.05,
                p_danger=0.05,
                escalated=False,
                escalated_to=None,
                decision_rule_version="v1.0",
                model_version="model-v1",
                adapter_sha256="sha256test",
                scored_at=now,
            )
        )

    # Historical row for history endpoint
    _test_db.add(
        McpLlmAxisScore(
            server_id="srv-ev-1",
            axis_name="overall_risk",
            label="MEDIUM",
            label_index=1,
            probs={"LOW": 0.5, "MEDIUM": 0.4, "HIGH": 0.1},
            p_top=0.5,
            p_critical=0.1,
            p_danger=0.3,
            escalated=False,
            escalated_to=None,
            decision_rule_version="v0.9",
            model_version="model-v0",
            adapter_sha256="sha256old",
            scored_at=datetime(2026, 1, 1),
        )
    )
    _test_db.commit()

    def _override():
        try:
            yield _test_db
        finally:
            pass

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    # --- Happy path: get_server_evidence_bundle ---
    resp = client.get("/api/score-axis-evidence/srv-ev-1")
    if resp.status_code != 200:
        print(f"FAIL: get_server_evidence_bundle returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    bundle = resp.json()
    if "axes" not in bundle or len(bundle["axes"]) < 2:
        print(f"FAIL: malformed bundle: {bundle}")
        sys.exit(1)

    # --- Happy path: get_axis_evidence ---
    resp2 = client.get("/api/score-axis-evidence/srv-ev-1/overall_risk")
    if resp2.status_code != 200:
        print(f"FAIL: get_axis_evidence returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    axis_data = resp2.json()
    if axis_data.get("label") != "LOW":
        print(f"FAIL: wrong label: {axis_data}")
        sys.exit(1)

    # --- Happy path: get_axis_history ---
    resp3 = client.get(
        "/api/score-axis-evidence/srv-ev-1/overall_risk/history?limit=10"
    )
    if resp3.status_code != 200:
        print(f"FAIL: get_axis_history returned {resp3.status_code}: {resp3.text}")
        sys.exit(1)
    history = resp3.json()
    if len(history.get("history", [])) < 2:
        print(f"FAIL: expected >= 2 history rows: {history}")
        sys.exit(1)

    # --- Happy path: list_scores ---
    resp4 = client.get(
        "/api/score-axis-evidence/scores?server_id=srv-ev-1&limit=10"
    )
    if resp4.status_code != 200:
        print(f"FAIL: list_scores returned {resp4.status_code}: {resp4.text}")
        sys.exit(1)
    scores_data = resp4.json()
    if scores_data.get("total", 0) < 3:
        print(f"FAIL: expected >= 3 scores: {scores_data}")
        sys.exit(1)

    # --- Happy path: get_server_verdict ---
    resp5 = client.get("/api/score-axis-evidence/servers/srv-ev-1/verdict")
    if resp5.status_code != 200:
        print(f"FAIL: get_server_verdict returned {resp5.status_code}: {resp5.text}")
        sys.exit(1)
    verdict = resp5.json()
    if "axes" not in verdict or "overall_risk" not in verdict["axes"]:
        print(f"FAIL: malformed verdict: {verdict}")
        sys.exit(1)

    # --- Validation failure: invalid axis ---
    resp6 = client.get("/api/score-axis-evidence/srv-ev-1/invalid_axis")
    if resp6.status_code != 400:
        print(f"FAIL: expected 400 for invalid axis, got {resp6.status_code}")
        sys.exit(1)

    # --- Not found ---
    resp7 = client.get("/api/score-axis-evidence/nonexistent/overall_risk")
    if resp7.status_code != 404:
        print(f"FAIL: expected 404 for nonexistent server, got {resp7.status_code}")
        sys.exit(1)

    # --- No session override (should fail gracefully) ---
    app.dependency_overrides.clear()
    resp8 = client.get("/api/score-axis-evidence/srv-ev-1")
    if resp8.status_code == 200:
        print(f"FAIL: expected non-200 without session override, got {resp8.status_code}")
        sys.exit(1)

    print("PASS")
