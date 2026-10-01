# deps: fastapi, pydantic, sqlalchemy
"""Scoring Coverage Audit API -- detailed per-axis coverage audit.

GET /api/scoring/coverage
  Returns per-axis coverage statistics for MCP server scoring:
  what fraction of servers have scores on each of the 7 risk axes.

Auth: public.
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry / mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on path for app imports
_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_coverage_audit"])

# The 7 risk axes from the real schema
ALL_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #


class AxisCoverageDetail(BaseModel):
    axis_name: str
    servers_scored: int = Field(..., description="Count of servers with this axis scored")
    servers_missing: int = Field(..., description="Count of servers missing this axis")
    coverage_pct: float = Field(..., description="Percentage of servers scored on this axis")


class UncoveredServer(BaseModel):
    server_id: str
    name: str | None
    missing_axes: list[str] = Field(..., description="List of axes with no score for this server")


class ScoringCoverageAuditResponse(BaseModel):
    total_servers: int = Field(..., description="Total servers in registry")
    fully_scored: int = Field(..., description="Servers with all 7 axes scored")
    partially_scored: int = Field(..., description="Servers with some but not all axes scored")
    never_scored: int = Field(..., description="Servers with no axis scores at all")
    axis_details: list[AxisCoverageDetail] = Field(..., description="Per-axis coverage breakdown")
    uncovered_servers: list[UncoveredServer] = Field(..., description="Servers with missing axes")
    audited_at: datetime = Field(..., description="Timestamp of this audit")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _compute_audit(session: Session) -> dict:
    """Compute full audit stats from the session."""
    # All servers
    all_servers = session.query(McpServerRegistry.server_id, McpServerRegistry.name).all()
    total = len(all_servers)
    server_id_to_name = {sid: name for sid, name in all_servers}

    # All axis scores, keyed by (server_id, axis_name)
    scores = session.query(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name).all()

    scored_by_server: dict[str, set[str]] = {}
    for server_id, axis_name in scores:
        scored_by_server.setdefault(server_id, set()).add(axis_name)

    fully_scored = 0
    partially_scored = 0
    never_scored = 0
    uncovered: list[dict] = []

    for server_id, name in all_servers:
        axes = scored_by_server.get(server_id, set())
        if not axes:
            never_scored += 1
            uncovered.append({
                "server_id": server_id,
                "name": name,
                "missing_axes": ALL_AXES[:],
            })
        elif set(axes) >= set(ALL_AXES):
            fully_scored += 1
        else:
            partially_scored += 1
            missing = [ax for ax in ALL_AXES if ax not in axes]
            uncovered.append({
                "server_id": server_id,
                "name": name,
                "missing_axes": missing,
            })

    # Per-axis coverage
    axis_details = []
    for ax in ALL_AXES:
        scored_count = sum(1 for s_axes in scored_by_server.values() if ax in s_axes)
        missing_count = total - scored_count
        pct = round((scored_count / total) * 100, 2) if total else 0.0
        axis_details.append({
            "axis_name": ax,
            "servers_scored": scored_count,
            "servers_missing": missing_count,
            "coverage_pct": pct,
        })

    return {
        "total_servers": total,
        "fully_scored": fully_scored,
        "partially_scored": partially_scored,
        "never_scored": never_scored,
        "axis_details": axis_details,
        "uncovered_servers": uncovered,
        "audited_at": datetime.now(timezone.utc),
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get("/scoring/coverage", response_model=ScoringCoverageAuditResponse)
def get_scoring_coverage_audit(
    session: Session = Depends(get_session),
) -> ScoringCoverageAuditResponse:
    """
    Return scoring coverage audit: per-axis breakdown of which servers are missing
    scores on each of the 7 risk axes.
    """
    stats = _compute_audit(session)
    return ScoringCoverageAuditResponse(**stats)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    sys.path.insert(0, "/home/workspace/zo_sentinel")
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Seed: 5 servers with varied coverage
    # srv1: all 7 axes (fully scored)
    # srv2: 5 axes (partially scored)
    # srv3: 3 axes (partially scored)
    # srv4: 0 axes (never scored)
    # srv5: 2 axes (partially scored)
    with TestSessionLocal() as db:
        db.add(McpServerRegistry(server_id="srv1", name="Full Alpha"))
        db.add(McpServerRegistry(server_id="srv2", name="Partial Beta"))
        db.add(McpServerRegistry(server_id="srv3", name="Partial Gamma"))
        db.add(McpServerRegistry(server_id="srv4", name="Never Delta"))
        db.add(McpServerRegistry(server_id="srv5", name="Partial Epsilon"))
        db.commit()

        # srv1: all 7
        for ax in ALL_AXES:
            db.add(McpLlmAxisScore(
                id=hash(f"srv1-{ax}") & 0x7FFFFFFF,
                server_id="srv1", axis_name=ax,
                model_version="v1", label="medium", label_index=2,
                p_critical=0.1, p_danger=0.3, p_top=0.6, probs={},
                scored_at=datetime.now(timezone.utc),
            ))
        # srv2: 5 axes (no exploit_surface, network_egress)
        for ax in ALL_AXES[:5]:
            db.add(McpLlmAxisScore(
                id=hash(f"srv2-{ax}") & 0x7FFFFFFF,
                server_id="srv2", axis_name=ax,
                model_version="v1", label="medium", label_index=2,
                p_critical=0.1, p_danger=0.3, p_top=0.6, probs={},
                scored_at=datetime.now(timezone.utc),
            ))
        # srv3: 3 axes
        for ax in ALL_AXES[:3]:
            db.add(McpLlmAxisScore(
                id=hash(f"srv3-{ax}") & 0x7FFFFFFF,
                server_id="srv3", axis_name=ax,
                model_version="v1", label="medium", label_index=2,
                p_critical=0.1, p_danger=0.3, p_top=0.6, probs={},
                scored_at=datetime.now(timezone.utc),
            ))
        # srv4: none
        # srv5: 2 axes
        for ax in ALL_AXES[5:]:
            db.add(McpLlmAxisScore(
                id=hash(f"srv5-{ax}") & 0x7FFFFFFF,
                server_id="srv5", axis_name=ax,
                model_version="v1", label="medium", label_index=2,
                p_critical=0.1, p_danger=0.3, p_top=0.6, probs={},
                scored_at=datetime.now(timezone.utc),
            ))
        db.commit()

    def _override():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    client = TestClient(test_app)
    resp = client.get("/api/scoring/coverage")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    assert data["total_servers"] == 5, f"total_servers: expected 5, got {data['total_servers']}"
    assert data["fully_scored"] == 1, f"fully_scored: expected 1, got {data['fully_scored']}"
    assert data["partially_scored"] == 3, f"partially_scored: expected 3, got {data['partially_scored']}"
    assert data["never_scored"] == 1, f"never_scored: expected 1, got {data['never_scored']}"

    # Verify axis detail counts
    axis_details = {d["axis_name"]: d for d in data["axis_details"]}
    assert len(axis_details) == 7, f"Expected 7 axes, got {len(axis_details)}"

    # overall_risk: 4 servers have it (srv1,2,3,5) = 80%
    assert axis_details["overall_risk"]["servers_scored"] == 4
    assert axis_details["overall_risk"]["servers_missing"] == 1
    assert axis_details["overall_risk"]["coverage_pct"] == 80.0

    # exploit_surface: 2 servers have it (srv1, srv5) = 40%
    assert axis_details["exploit_surface"]["servers_scored"] == 2
    assert axis_details["exploit_surface"]["servers_missing"] == 3
    assert axis_details["exploit_surface"]["coverage_pct"] == 40.0

    # uncovered_servers: 4 total (never_scored srv4 + 3 partial)
    assert len(data["uncovered_servers"]) == 4

    # Check structure of uncovered
    for item in data["uncovered_servers"]:
        assert "server_id" in item
        assert "name" in item
        assert "missing_axes" in item
        assert isinstance(item["missing_axes"], list)

    assert "audited_at" in data
    print("PASS")
    sys.exit(0)
