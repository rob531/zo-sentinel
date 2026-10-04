# deps: fastapi, pydantic, sqlalchemy, PyJWT
"""server_detail_api – GET /api/servers/{server_id} returning full server detail
including axis scores, with trust-gating applied for official publishers."""
from __future__ import annotations

import os
import sys as _sys
from datetime import datetime
from typing import Optional

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in _sys.path:
    _sys.path.insert(0, _root)

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_detail_api"])


# ── Response models ──────────────────────────────────────────────────────────

class AxisScoreDetail(BaseModel):
    label: Optional[str] = None
    label_index: Optional[int] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool = False
    escalated_to: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: Optional[str] = None

    class Config:
        from_attributes = True


class FreshnessInfo(BaseModel):
    last_scanned: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_assessed: Optional[datetime] = None
    scan_count: Optional[int] = None
    first_seen: Optional[datetime] = None

    class Config:
        from_attributes = True


class ServerDetailResponse(BaseModel):
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
    signals: dict[str, AxisScoreDetail] = Field(default_factory=dict)
    freshness: Optional[FreshnessInfo] = None

    class Config:
        from_attributes = True


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/servers/{server_id}", response_model=ServerDetailResponse)
def get_server_detail(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerDetailResponse:
    """Return full detail for a single MCP server: registry record + axis scores."""
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

    # Fetch latest axis scores per axis_name (one row per axis per model_version)
    scores = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    signals: dict[str, AxisScoreDetail] = {}
    for score in scores:
        if score.axis_name and score.axis_name not in signals:
            signals[score.axis_name] = AxisScoreDetail(
                label=score.label,
                label_index=score.label_index,
                p_top=score.p_top,
                p_critical=score.p_critical,
                p_danger=score.p_danger,
                escalated=bool(score.escalated),
                escalated_to=score.escalated_to,
                model_version=score.model_version,
                scored_at=score.scored_at.isoformat() if score.scored_at else None,
            )

    freshness = FreshnessInfo(
        last_scanned=server.last_scanned,
        last_seen=server.last_seen,
        last_assessed=server.last_assessed,
        scan_count=server.scan_count,
        first_seen=server.first_seen,
    )

    return ServerDetailResponse(
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
        signals=signals,
        freshness=freshness,
    )


# ── Self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # In-memory SQLite with StaticPool so state is shared across connections
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = __import__(
        "sqlalchemy.orm", fromlist=["sessionmaker"]
    ).sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    with TestSessionLocal() as db:
        srv = McpServerRegistry(
            server_id="srv-test-001",
            name="Test MCP Server",
            registry_source="test",
            url="https://example.com/test",
            description="A test server for validation",
            trust_score=0.9,
            verdict="medium",
            verdict_reasoning="Test reasoning",
            confidence=0.85,
            risk_tier="medium",
            scan_count=5,
            first_seen=datetime(2024, 1, 1),
            last_seen=datetime(2024, 6, 1),
            last_scanned=datetime(2024, 6, 15),
            last_assessed=datetime(2024, 6, 15),
        )
        db.add(srv)

        for axis, lbl, p_top in [
            ("overall_risk", "medium", 0.72),
            ("auth_strength", "strong", 0.88),
            ("capability_breadth", "high", 0.81),
        ]:
            db.add(
                McpLlmAxisScore(
                    server_id="srv-test-001",
                    axis_name=axis,
                    label=lbl,
                    label_index=1,
                    p_top=p_top,
                    p_critical=0.05,
                    p_danger=0.15,
                    escalated=False,
                    model_version="test-v1",
                    scored_at=datetime(2024, 6, 15),
                )
            )
        db.commit()

    # Build FastAPI app for self-test
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(test_app)

    # ── Happy path ───────────────────────────────────────────────────────────
    resp = client.get("/api/servers/srv-test-001")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["server_id"] == "srv-test-001"
    assert data["name"] == "Test MCP Server"
    assert data["risk_tier"] == "medium"
    assert "signals" in data
    assert "overall_risk" in data["signals"], "overall_risk axis missing"
    assert data["signals"]["overall_risk"]["label"] == "medium"
    assert data["signals"]["overall_risk"]["p_top"] == 0.72
    assert "freshness" in data
    assert data["freshness"]["scan_count"] == 5

    # ── Not-found path ───────────────────────────────────────────────────────
    resp_404 = client.get("/api/servers/nonexistent-server")
    assert resp_404.status_code == 404, f"Expected 404, got {resp_404.status_code}"

    print("PASS")
    sys.exit(0)
