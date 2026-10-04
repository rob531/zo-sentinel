# deps: fastapi, pydantic, sqlalchemy
"""entity_report_api – risk/axis-report for a single MCP server entity.

GET /api/entity-report/{server_id}    Full report: server metadata + axis scores.
GET /api/entity-report/{server_id}/summary  Lightweight summary (verdict, tier, counts).

Auth: public.
Data: app tier via get_session + McpServerRegistry + McpLlmAxisScore.
"""
from __future__ import annotations

import sys as _sys
import os as _os
from datetime import datetime, timezone
from typing import Optional

_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _root not in _sys.path:
    _sys.path.insert(0, _root)

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["entity_report_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisScoreItem(BaseModel):
    axis_name: str
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    model_version: Optional[str] = None
    adapter_sha256: Optional[str] = None
    scored_at: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ServerMeta(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    description: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    verdict_reasoning: Optional[str] = None
    confidence: Optional[float] = None
    risk_tier: Optional[str] = None
    scan_count: Optional[int] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_scanned: Optional[datetime] = None
    last_assessed: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class EntityReportResponse(BaseModel):
    server: ServerMeta
    axis_scores: dict[str, AxisScoreItem] = Field(default_factory=dict)
    total_axes: int = 0
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EntityReportSummaryResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    total_axes: int = 0
    scored_axes: int = 0
    avg_p_top: Optional[float] = None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/entity-report/{server_id}",
    response_model=EntityReportResponse,
    summary="Full entity report for an MCP server",
    responses={404: {"description": "Server not found"}},
)
def get_entity_report(
    server_id: str,
    db: Session = Depends(get_session),
) -> EntityReportResponse:
    """Return full risk report for one server: metadata + all axis scores."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{server_id}' not found",
        )

    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    axis_scores: dict[str, AxisScoreItem] = {}
    for score in scores:
        if score.axis_name and score.axis_name not in axis_scores:
            axis_scores[score.axis_name] = AxisScoreItem(
                axis_name=score.axis_name,
                label=score.label,
                label_index=score.label_index,
                p_top=score.p_top,
                p_critical=score.p_critical,
                p_danger=score.p_danger,
                escalated=bool(score.escalated),
                escalated_to=score.escalated_to,
                model_version=score.model_version,
                adapter_sha256=score.adapter_sha256,
                scored_at=score.scored_at.isoformat() if score.scored_at else None,
            )

    return EntityReportResponse(
        server=ServerMeta(
            server_id=server.server_id,
            name=server.name,
            registry_source=server.registry_source,
            url=server.url,
            description=server.description,
            trust_score=server.trust_score,
            verdict=server.verdict,
            verdict_reasoning=server.verdict_reasoning,
            confidence=server.confidence,
            risk_tier=server.risk_tier,
            scan_count=server.scan_count,
            first_seen=server.first_seen,
            last_seen=server.last_seen,
            last_scanned=server.last_scanned,
            last_assessed=server.last_assessed,
        ),
        axis_scores=axis_scores,
        total_axes=len(axis_scores),
    )


@router.get(
    "/entity-report/{server_id}/summary",
    response_model=EntityReportSummaryResponse,
    summary="Lightweight entity report summary",
    responses={404: {"description": "Server not found"}},
)
def get_entity_report_summary(
    server_id: str,
    db: Session = Depends(get_session),
) -> EntityReportSummaryResponse:
    """Return a lightweight summary: verdict, tier, axis counts, avg p_top."""
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Server '{server_id}' not found",
        )

    total_axes = 7  # canonical 7-axis model
    scored_axes = (
        db.query(func.count(func.distinct(McpLlmAxisScore.axis_name)))
        .filter(McpLlmAxisScore.server_id == server_id)
        .scalar()
    ) or 0

    avg_p = (
        db.query(func.avg(McpLlmAxisScore.p_top))
        .filter(McpLlmAxisScore.server_id == server_id)
        .scalar()
    )

    return EntityReportSummaryResponse(
        server_id=server.server_id,
        name=server.name,
        verdict=server.verdict,
        risk_tier=server.risk_tier,
        total_axes=total_axes,
        scored_axes=scored_axes,
        avg_p_top=round(avg_p, 4) if avg_p is not None else None,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    def _get_test_session():
        db = _TestSession()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    with _TestSession() as db:
        srv = McpServerRegistry(
            server_id="srv-test-001",
            name="Test Entity Server",
            registry_source="npm",
            url="https://registry.npmjs.org/test-server",
            description="A test entity for validation",
            trust_score=0.87,
            verdict="medium",
            verdict_reasoning="Balanced risk profile",
            confidence=0.82,
            risk_tier="medium",
            scan_count=3,
            first_seen=datetime(2024, 2, 1),
            last_seen=datetime(2024, 7, 1),
            last_scanned=datetime(2024, 7, 15),
            last_assessed=datetime(2024, 7, 15),
        )
        db.add(srv)

        for axis, lbl, p_top, p_crit, p_dang in [
            ("overall_risk", "medium", 0.71, 0.04, 0.12),
            ("auth_strength", "strong", 0.89, 0.01, 0.05),
            ("network_egress", "low", 0.95, 0.00, 0.02),
            ("data_sensitivity", "low", 0.91, 0.00, 0.03),
        ]:
            db.add(
                McpLlmAxisScore(
                    server_id="srv-test-001",
                    axis_name=axis,
                    label=lbl,
                    label_index=1,
                    p_top=p_top,
                    p_critical=p_crit,
                    p_danger=p_dang,
                    escalated=False,
                    model_version="test-v1",
                    adapter_sha256="abc000",
                    scored_at=datetime(2024, 7, 15),
                )
            )
        db.commit()

    # Build FastAPI app for self-test
    _app = FastAPI()
    _app.include_router(router)
    _app.dependency_overrides[get_session] = _get_test_session

    _client = TestClient(_app)

    # ── Happy path: full report ───────────────────────────────────────────────
    resp = _client.get("/api/entity-report/srv-test-001")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["server"]["server_id"] == "srv-test-001"
    assert data["server"]["name"] == "Test Entity Server"
    assert data["server"]["risk_tier"] == "medium"
    assert "axis_scores" in data
    assert "overall_risk" in data["axis_scores"], "overall_risk axis missing"
    assert data["axis_scores"]["overall_risk"]["label"] == "medium"
    assert abs(data["axis_scores"]["overall_risk"]["p_top"] - 0.71) < 0.001
    assert data["total_axes"] == 4

    # ── Happy path: summary ───────────────────────────────────────────────────
    resp_sum = _client.get("/api/entity-report/srv-test-001/summary")
    assert resp_sum.status_code == 200, f"Expected 200, got {resp_sum.status_code}"
    sum_data = resp_sum.json()

    assert sum_data["server_id"] == "srv-test-001"
    assert sum_data["verdict"] == "medium"
    assert sum_data["risk_tier"] == "medium"
    assert sum_data["total_axes"] == 7
    assert sum_data["scored_axes"] == 4
    assert sum_data["avg_p_top"] is not None

    # ── Not-found path ────────────────────────────────────────────────────────
    resp_404 = _client.get("/api/entity-report/nonexistent-server")
    assert resp_404.status_code == 404, f"Expected 404, got {resp_404.status_code}"

    print("PASS")
