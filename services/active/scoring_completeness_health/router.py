# deps: fastapi, pydantic, sqlalchemy
"""Scoring Completeness Health API.

Reports health/completeness of the scoring pipeline: whether all 7 axes are
scored for each server, how complete the overall registry coverage is, and
whether scoring has stalled.

GET /api/scoring/completeness/health
    Returns overall scoring-completeness health: registry coverage,
    axis-level completeness, per-axis row counts, completeness percentage,
    and a health status label.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM on mcp_server_registry /
  mcp_llm_axis_scores.
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

# Ensure repo root is on the import path so `from app.db` resolves
_repo = _Path(__file__).resolve().parents[3]
if str(_repo) not in _sys.path:
    _sys.path.insert(0, str(_repo))

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api/scoring/completeness", tags=["scoring_completeness_health"])


# --------------------------------------------------------------------------- #
# Constants
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

_HEALTHY_COMPLETENESS_PCT = 90.0


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class AxisCompleteness(BaseModel):
    axis_name: str
    rows_scored: int
    coverage_pct: float


class CompletenessHealthResponse(BaseModel):
    generated_at: str
    total_registry_servers: int
    total_scored_servers: int
    total_unscored_servers: int
    fully_complete_servers: int
    partially_complete_servers: int
    completeness_pct: float
    partially_complete_pct: float
    total_axis_rows: int
    by_axis: list[AxisCompleteness]
    health_status: str
    health_reason: Optional[str]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _health_status(completeness_pct: float, total_registry: int, total_scored: int) -> tuple[str, Optional[str]]:
    if total_registry == 0:
        return "DEGRADED", "No servers in registry"
    if completeness_pct >= _HEALTHY_COMPLETENESS_PCT:
        return "HEALTHY", None
    if completeness_pct >= 50.0:
        return "DEGRADED", f"Completeness {completeness_pct:.1f}% below {_HEALTHY_COMPLETENESS_PCT}%"
    return "UNHEALTHY", f"Completeness {completeness_pct:.1f}% below {_HEALTHY_COMPLETENESS_PCT}%"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/health",
    response_model=CompletenessHealthResponse,
    summary="Get scoring completeness health across the registry",
)
def completeness_health(
    db: Session = Depends(get_session),
) -> CompletenessHealthResponse:
    """
    Return scoring-completeness health across the MCP server registry.

    A server is **fully complete** when it has at least one row for each of the
    7 LLM axes. A server is **partially complete** when it has at least one axis
    row but is missing at least one. A server is **unscored** when it has no
    axis rows at all.

    The overall health status is:

    - **HEALTHY**: >= 90% of registry servers are fully complete.
    - **DEGRADED**: 50–90% fully complete.
    - **UNHEALTHY**: < 50% fully complete.
    """
    now_ts = _now()

    # Total registry servers
    total_registry: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    # All axis-score rows
    all_rows = (
        db.execute(
            select(McpLlmAxisScore).order_by(McpLlmAxisScore.server_id)
        )
        .scalars()
        .all()
    )

    # Index scored rows by server_id: build set of axes per server
    server_axes: dict[str, set[str]] = {}
    for row in all_rows:
        sid = row.server_id
        if sid not in server_axes:
            server_axes[sid] = set()
        server_axes[sid].add(row.axis_name)

    total_scored_servers = len(server_axes)
    total_unscored_servers = max(0, total_registry - total_scored_servers)

    fully_complete = sum(
        1 for _sid, axes in server_axes.items()
        if len(axes) >= len(ALL_AXES)
    )
    partially_complete = sum(
        1 for _sid, axes in server_axes.items()
        if 0 < len(axes) < len(ALL_AXES)
    )

    completeness_pct = round((fully_complete / total_registry) * 100, 2) if total_registry else 0.0
    partial_pct = round((partially_complete / total_registry) * 100, 2) if total_registry else 0.0

    health, reason = _health_status(completeness_pct, total_registry, total_scored_servers)

    # Per-axis completeness
    axis_counts: dict[str, int] = {ax: 0 for ax in ALL_AXES}
    for _sid, axes in server_axes.items():
        for ax in axes:
            if ax in axis_counts:
                axis_counts[ax] += 1

    by_axis = [
        AxisCompleteness(
            axis_name=ax,
            rows_scored=axis_counts[ax],
            coverage_pct=round((axis_counts[ax] / total_registry) * 100, 2) if total_registry else 0.0,
        )
        for ax in ALL_AXES
    ]

    return CompletenessHealthResponse(
        generated_at=now_ts.isoformat(),
        total_registry_servers=total_registry,
        total_scored_servers=total_scored_servers,
        total_unscored_servers=total_unscored_servers,
        fully_complete_servers=fully_complete,
        partially_complete_servers=partially_complete,
        completeness_pct=completeness_pct,
        partially_complete_pct=partial_pct,
        total_axis_rows=len(all_rows),
        by_axis=by_axis,
        health_status=health,
        health_reason=reason,
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

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    now = _now()

    # Seed data: 5 registry servers
    # srv-000: fully complete (all 7 axes)
    # srv-001: partial (3 axes: overall_risk, auth_strength, capability_breadth)
    #          Note: auth_strength also appears in srv-000's full set, so it has 2 rows
    # srv-002..srv-004: unscored
    with TestSessionLocal() as sess:
        for i in range(5):
            sess.add(McpServerRegistry(
                server_id=f"srv-{i:03d}",
                name=f"Server {i}",
                registry_source="test",
                risk_tier="MEDIUM",
                first_seen=now,
                last_scanned=now,
            ))

        # srv-000: fully complete (all 7 axes) — ids 1..7
        for idx, ax in enumerate(ALL_AXES):
            sess.add(McpLlmAxisScore(
                id=idx + 1,
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

        # srv-001: partial (3 axes: overall_risk, auth_strength, capability_breadth) — ids 8..10
        for idx, ax in enumerate(["overall_risk", "auth_strength", "capability_breadth"]):
            sess.add(McpLlmAxisScore(
                id=len(ALL_AXES) + 1 + idx,
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

        sess.commit()

    client = TestClient(test_app)

    # Happy path
    resp = client.get("/api/scoring/completeness/health")
    if resp.status_code != 200:
        print(f"FAIL: expected 200, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    data = resp.json()
    expected_keys = {
        "generated_at", "total_registry_servers", "total_scored_servers",
        "total_unscored_servers", "fully_complete_servers",
        "partially_complete_servers", "completeness_pct", "partially_complete_pct",
        "total_axis_rows", "by_axis", "health_status", "health_reason",
    }
    if not expected_keys.issubset(data.keys()):
        print(f"FAIL: missing keys. Got {set(data.keys())}, expected {expected_keys}")
        sys.exit(1)

    if data["total_registry_servers"] != 5:
        print(f"FAIL: total_registry_servers expected 5, got {data['total_registry_servers']}")
        sys.exit(1)

    if data["total_scored_servers"] != 2:
        print(f"FAIL: total_scored_servers expected 2, got {data['total_scored_servers']}")
        sys.exit(1)

    if data["total_unscored_servers"] != 3:
        print(f"FAIL: total_unscored_servers expected 3, got {data['total_unscored_servers']}")
        sys.exit(1)

    if data["fully_complete_servers"] != 1:
        print(f"FAIL: fully_complete_servers expected 1, got {data['fully_complete_servers']}")
        sys.exit(1)

    if data["partially_complete_servers"] != 1:
        print(f"FAIL: partially_complete_servers expected 1, got {data['partially_complete_servers']}")
        sys.exit(1)

    if data["completeness_pct"] != 20.0:
        print(f"FAIL: completeness_pct expected 20.0, got {data['completeness_pct']}")
        sys.exit(1)

    if data["total_axis_rows"] != 10:
        print(f"FAIL: total_axis_rows expected 10, got {data['total_axis_rows']}")
        sys.exit(1)

    by_axis_map = {e["axis_name"]: e for e in data["by_axis"]}
    # overall_risk appears in both srv-000 and srv-001 → 2 rows
    if by_axis_map["overall_risk"]["rows_scored"] != 2:
        print(f"FAIL: overall_risk rows_scored expected 2, got {by_axis_map['overall_risk']['rows_scored']}")
        sys.exit(1)
    # auth_strength appears in both srv-000 and srv-001 → 2 rows
    if by_axis_map["auth_strength"]["rows_scored"] != 2:
        print(f"FAIL: auth_strength rows_scored expected 2, got {by_axis_map['auth_strength']['rows_scored']}")
        sys.exit(1)
    # exploit_surface appears only in srv-000 → 1 row
    if by_axis_map["exploit_surface"]["rows_scored"] != 1:
        print(f"FAIL: exploit_surface rows_scored expected 1, got {by_axis_map['exploit_surface']['rows_scored']}")
        sys.exit(1)

    # 20% < 50% → UNHEALTHY
    if data["health_status"] != "UNHEALTHY":
        print(f"FAIL: health_status expected UNHEALTHY (20% < 50%), got {data['health_status']}")
        sys.exit(1)

    if data["health_reason"] is None:
        print("FAIL: health_reason should be set for UNHEALTHY status")
        sys.exit(1)

    if "20.0%" not in data["health_reason"]:
        print(f"FAIL: health_reason should mention 20.0%, got {data['health_reason']}")
        sys.exit(1)

    if len(data["by_axis"]) != 7:
        print(f"FAIL: by_axis should have 7 entries, got {len(data['by_axis'])}")
        sys.exit(1)

    axis_names_found = {e["axis_name"] for e in data["by_axis"]}
    if axis_names_found != set(ALL_AXES):
        print(f"FAIL: by_axis axis names mismatch. Got {axis_names_found}, expected {set(ALL_AXES)}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)
