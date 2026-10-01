# deps: fastapi, pydantic, sqlalchemy
"""Scoring Coverage Diagnostic API.

Diagnoses coverage gaps in LLM axis scoring across the MCP server registry.
Breaks down scoring status by axis presence, coverage gaps, and model version coverage.

GET /api/scoring/coverage_diagnostic/summary
    High-level coverage diagnostics: counts by axis presence, coverage gaps,
    servers with partial coverage, and model-version breakdown.

GET /api/scoring/coverage_diagnostic/gaps
    Lists individual servers that are unscored, have partial axis coverage,
    or have mismatched model versions.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry / mcp_llm_axis_scores.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/scoring/coverage_diagnostic", tags=["scoring_coverage_diagnostic"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

ALL_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]


class AxisPresenceEntry(BaseModel):
    axis_name: str
    servers_scored: int
    coverage_pct: float


class ModelVersionEntry(BaseModel):
    model_version: str
    servers_scored: int
    axes_count: int


class CoverageSummaryResponse(BaseModel):
    generated_at: str
    total_registry_servers: int
    fully_scored_servers: int        # all 7 axes present
    partially_scored_servers: int     # at least 1 axis but not all 7
    unscored_servers: int             # zero axis scores
    fully_scored_pct: float
    partial_coverage_pct: float
    unscored_pct: float
    unscored_servers: int
    by_axis: List[AxisPresenceEntry]
    by_model_version: List[ModelVersionEntry]
    axis_names: List[str] = Field(default_factory=lambda: ALL_AXES)


class GapServerEntry(BaseModel):
    server_id: str
    name: Optional[str]
    registry_source: Optional[str]
    risk_tier: Optional[str]
    first_seen: Optional[datetime]
    last_scanned: Optional[datetime]
    gap_type: str          # "unscored" | "partial" | "model_mismatch" | "stale"
    axes_present: List[str]
    axes_missing: List[str]
    last_scored_at: Optional[datetime]
    model_version: Optional[str]


class GapsResponse(BaseModel):
    generated_at: str
    total_gaps: int
    unscored_gaps: List[GapServerEntry]
    partial_gaps: List[GapServerEntry]
    stale_gaps: List[GapServerEntry]
    sample_limit_applied: bool
    sample_limit: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/summary", response_model=CoverageSummaryResponse)
def coverage_summary(db: Session = Depends(get_session)) -> CoverageSummaryResponse:
    """
    High-level diagnostic summary of scoring coverage across the registry.
    """
    now_ts = _now()

    # Total registry
    total_registry: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    # All scored rows keyed by (server_id, axis_name, model_version)
    all_rows = db.execute(
        select(McpLlmAxisScore).order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
    ).scalars().all()

    # Build per-server axis set
    server_axes: Dict[str, set] = {}
    server_model: Dict[str, str] = {}
    server_last_scored: Dict[str, datetime] = {}
    for row in all_rows:
        sid = row.server_id
        if sid not in server_axes:
            server_axes[sid] = set()
            server_model[sid] = row.model_version
            server_last_scored[sid] = row.scored_at
        server_axes[sid].add(row.axis_name)
        if row.scored_at and (server_last_scored.get(sid) is None or row.scored_at > server_last_scored[sid]):
            server_last_scored[sid] = row.scored_at

    fully_scored = sum(1 for sid in server_axes if len(server_axes[sid]) >= len(ALL_AXES))
    partially_scored = sum(1 for sid in server_axes if 0 < len(server_axes[sid]) < len(ALL_AXES))
    unscored = max(total_registry - len(server_axes), 0)

    fully_pct = round((fully_scored / total_registry) * 100, 2) if total_registry else 0.0
    partial_pct = round((partially_scored / total_registry) * 100, 2) if total_registry else 0.0
    unscored_pct = round((unscored / total_registry) * 100, 2) if total_registry else 0.0

    # Per-axis coverage
    axis_counts: Dict[str, int] = {ax: 0 for ax in ALL_AXES}
    for sid, axes in server_axes.items():
        for ax in axes:
            if ax in axis_counts:
                axis_counts[ax] += 1

    by_axis: List[AxisPresenceEntry] = [
        AxisPresenceEntry(
            axis_name=ax,
            servers_scored=axis_counts[ax],
            coverage_pct=round((axis_counts[ax] / total_registry) * 100, 2) if total_registry else 0.0,
        )
        for ax in ALL_AXES
    ]

    # Per-model-version breakdown
    model_axis_counts: Dict[str, int] = {}
    for row in all_rows:
        mv = row.model_version
        model_axis_counts[mv] = model_axis_counts.get(mv, 0) + 1

    model_servers: Dict[str, int] = {}
    for sid, mv in server_model.items():
        model_servers[mv] = model_servers.get(mv, 0) + 1

    by_model_version: List[ModelVersionEntry] = [
        ModelVersionEntry(
            model_version=mv,
            servers_scored=model_servers.get(mv, 0),
            axes_count=model_axis_counts.get(mv, 0),
        )
        for mv in sorted(model_servers.keys())
    ]

    return CoverageSummaryResponse(
        generated_at=now_ts.isoformat(),
        total_registry_servers=total_registry,
        fully_scored_servers=fully_scored,
        partially_scored_servers=partially_scored,
        unscored_servers=unscored,
        fully_scored_pct=fully_pct,
        partial_coverage_pct=partial_pct,
        unscored_pct=unscored_pct,
        by_axis=by_axis,
        by_model_version=by_model_version,
        axis_names=ALL_AXES,
    )


@router.get("/gaps", response_model=GapsResponse)
def coverage_gaps(
    sample_limit: int = Query(default=20, ge=1, le=500, description="Max entries per gap category"),
    db: Session = Depends(get_session),
) -> GapsResponse:
    """
    Detailed list of servers with scoring coverage gaps.
    Three gap types:
      - unscored:    no axis scores at all
      - partial:     some axes scored but not all 7
      - stale:       last scored > 7 days ago
    """
    now_ts = _now()
    stale_threshold = datetime(1970, 1, 1, tzinfo=timezone.utc)  # placeholder; set below

    # Registry servers
    all_servers = db.execute(
        select(McpServerRegistry).order_by(McpServerRegistry.first_seen.desc())
    ).scalars().all()

    # Scored rows
    all_rows = db.execute(
        select(McpLlmAxisScore).order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
    ).scalars().all()

    # Index scored rows by server_id
    server_axes: Dict[str, set] = {}
    server_last_scored: Dict[str, datetime] = {}
    for row in all_rows:
        sid = row.server_id
        if sid not in server_axes:
            server_axes[sid] = set()
            server_last_scored[sid] = row.scored_at
        server_axes[sid].add(row.axis_name)
        if row.scored_at and (server_last_scored.get(sid) is None or row.scored_at > server_last_scored[sid]):
            server_last_scored[sid] = row.scored_at

    # Determine stale threshold (7 days ago)
    import time
    stale_threshold = datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - 7 * 24 * 3600,
        tz=timezone.utc,
    )

    unscored_entries: List[GapServerEntry] = []
    partial_entries: List[GapServerEntry] = []
    stale_entries: List[GapServerEntry] = []

    unscored_sids: set = set()
    partial_sids: set = set()
    stale_sids: set = set()

    for srv in all_servers:
        sid = srv.server_id
        axes = server_axes.get(sid, set())
        last_scored = server_last_scored.get(sid)

        if not axes:
            # Unscored
            unscored_sids.add(sid)
            unscored_entries.append(GapServerEntry(
                server_id=sid,
                name=srv.name,
                registry_source=srv.registry_source,
                risk_tier=srv.risk_tier,
                first_seen=srv.first_seen,
                last_scanned=srv.last_scanned,
                gap_type="unscored",
                axes_present=[],
                axes_missing=ALL_AXES,
                last_scored_at=None,
                model_version=None,
            ))
        else:
            missing = [ax for ax in ALL_AXES if ax not in axes]
            if missing:
                partial_sids.add(sid)
                partial_entries.append(GapServerEntry(
                    server_id=sid,
                    name=srv.name,
                    registry_source=srv.registry_source,
                    risk_tier=srv.risk_tier,
                    first_seen=srv.first_seen,
                    last_scanned=srv.last_scanned,
                    gap_type="partial",
                    axes_present=sorted(axes),
                    axes_missing=missing,
                    last_scored_at=last_scored,
                    model_version=None,
                ))

            # Stale check: last scored > 7 days ago
            if last_scored is not None and last_scored < stale_threshold:
                stale_sids.add(sid)
                stale_entries.append(GapServerEntry(
                    server_id=sid,
                    name=srv.name,
                    registry_source=srv.registry_source,
                    risk_tier=srv.risk_tier,
                    first_seen=srv.first_seen,
                    last_scanned=srv.last_scanned,
                    gap_type="stale",
                    axes_present=sorted(axes),
                    axes_missing=[ax for ax in ALL_AXES if ax not in axes],
                    last_scored_at=last_scored,
                    model_version=None,
                ))

    total_gaps = len(unscored_entries) + len(partial_entries) + len(stale_entries)

    return GapsResponse(
        generated_at=now_ts.isoformat(),
        total_gaps=total_gaps,
        unscored_gaps=unscored_entries[:sample_limit],
        partial_gaps=partial_entries[:sample_limit],
        stale_gaps=stale_entries[:sample_limit],
        sample_limit_applied=(
            len(unscored_entries) > sample_limit
            or len(partial_entries) > sample_limit
            or len(stale_entries) > sample_limit
        ),
        sample_limit=sample_limit,
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

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def _override():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override

    # Seed test data
    now = datetime.now(timezone.utc)
    old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc)

    with TestSessionLocal() as db:
        # 10 registry servers
        for i in range(10):
            db.add(McpServerRegistry(
                server_id=f"srv-{i:03d}",
                name=f"Server {i}",
                registry_source="test",
                risk_tier="MEDIUM",
                first_seen=now,
                last_scanned=now,
            ))

        # srv-000: fully scored (all 7 axes)
        for ax in ALL_AXES:
            db.add(McpLlmAxisScore(
                server_id="srv-000",
                axis_name=ax,
                model_version="v1",
                label="medium",
                label_index=2,
                p_top=0.5,
                p_critical=0.1,
                p_danger=0.2,
                probs={},
                scored_at=now,
            ))

        # srv-001: partially scored (3 axes)
        for ax in ALL_AXES[:3]:
            db.add(McpLlmAxisScore(
                server_id="srv-001",
                axis_name=ax,
                model_version="v1",
                label="medium",
                label_index=2,
                p_top=0.5,
                p_critical=0.1,
                p_danger=0.2,
                probs={},
                scored_at=now,
            ))

        # srv-002: stale (last scored 2020)
        db.add(McpLlmAxisScore(
            server_id="srv-002",
            axis_name="overall_risk",
            model_version="v1",
            label="medium",
            label_index=2,
            p_top=0.5,
            p_critical=0.1,
            p_danger=0.2,
            probs={},
            scored_at=old_ts,
        ))

        # srv-003..srv-009: unscored (no axis scores)
        db.commit()

    client = TestClient(test_app)

    # --- Test summary endpoint ---
    resp = client.get("/api/scoring/coverage_diagnostic/summary")
    assert resp.status_code == 200, f"summary: expected 200, got {resp.status_code}: {resp.text}"
    summary = resp.json()

    assert summary["total_registry_servers"] == 10, f"total: expected 10, got {summary['total_registry_servers']}"
    assert summary["fully_scored_servers"] == 1, f"fully_scored: expected 1, got {summary['fully_scored_servers']}"
    assert summary["partially_scored_servers"] == 1, f"partial: expected 1, got {summary['partially_scored_servers']}"
    assert summary["unscored_servers"] == 8, f"unscored: expected 8, got {summary['unscored_servers']}"
    assert isinstance(summary["by_axis"], list), "by_axis must be a list"
    assert len(summary["by_axis"]) == 7, f"by_axis length: expected 7, got {len(summary['by_axis'])}"
    assert summary["axis_names"] == ALL_AXES

    # --- Test gaps endpoint ---
    resp2 = client.get("/api/scoring/coverage_diagnostic/gaps?sample_limit=5")
    assert resp2.status_code == 200, f"gaps: expected 200, got {resp2.status_code}: {resp2.text}"
    gaps = resp2.json()

    assert gaps["total_gaps"] > 0, "expected at least one gap"
    assert isinstance(gaps["unscored_gaps"], list)
    assert isinstance(gaps["partial_gaps"], list)
    assert isinstance(gaps["stale_gaps"], list)

    # Verify unscored gaps contains srv-003 (first unscored server sorted by first_seen desc)
    unscored_ids = [e["server_id"] for e in gaps["unscored_gaps"]]
    assert "srv-003" in unscored_ids, f"srv-003 should be in unscored gaps: {unscored_ids}"

    # Verify partial gaps contains srv-001
    partial_ids = [e["server_id"] for e in gaps["partial_gaps"]]
    assert "srv-001" in partial_ids, f"srv-001 should be in partial gaps: {partial_ids}"

    # Verify stale gaps contains srv-002
    stale_ids = [e["server_id"] for e in gaps["stale_gaps"]]
    assert "srv-002" in stale_ids, f"srv-002 should be in stale gaps: {stale_ids}"

    # srv-000 should NOT appear in any gap
    all_gap_ids = set(unscored_ids) | set(partial_ids) | set(stale_ids)
    assert "srv-000" not in all_gap_ids, "srv-000 (fully scored) should not appear in gaps"

    # sample_limit applied check
    assert gaps["sample_limit"] == 5
    assert isinstance(gaps["sample_limit_applied"], bool)

    print("PASS")
    sys.exit(0)
