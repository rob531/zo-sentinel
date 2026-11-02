"""axis_coverage_gap_report.py -- per-axis scoring-coverage gap report.

Reads the SFT per-axis scores (mcp_llm_axis_scores, current model_version) and
reports which of the 7 canonical risk axes are missing or stale per server.

The computation is a PURE function over row dicts (compute_axis_coverage);
the FastAPI endpoint supplies those rows from the real app data layer
(app.db / app.models) -- no inline stubs, no new scoring, no new verdicts.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

CANONICAL_AXES: Tuple[str, ...] = (
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface",
)

# A score older than this (spec freshness SLA: re-verdict every live MCP
# within 7 days) is reported as stale.
STALE_AFTER = timedelta(days=7)

router = APIRouter(prefix="/api", tags=["coverage"])


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_axis_coverage(
    rows: List[dict],
    *,
    axes: Tuple[str, ...] = CANONICAL_AXES,
    now: Optional[datetime] = None,
) -> dict:
    """Coverage gap over the canonical axes.

    rows: dicts with keys server_id, axis_name, p_top, scored_at.
    A server counts as covered on an axis when it has at least one score row
    for that axis. A server is stale when any of its axis scores is older
    than STALE_AFTER (a score with no timestamp counts as stale -- freshness
    cannot be proven). Rows for axes outside `axes` are ignored.
    """
    reference = _as_utc(now) or datetime.now(timezone.utc)
    wanted = set(axes)
    servers = set()
    covered_servers: Dict[str, set] = {a: set() for a in axes}
    stale = set()

    for row in rows or []:
        sid = row.get("server_id")
        axis = row.get("axis_name")
        if not sid or axis not in wanted:
            continue
        servers.add(sid)
        covered_servers[axis].add(sid)
        ts = _as_utc(row.get("scored_at"))
        if ts is None or (reference - ts) > STALE_AFTER:
            stale.add(sid)

    total = len(servers)
    covered = {a: len(covered_servers[a]) for a in axes}
    missing = {a: total - covered[a] for a in axes}
    pct = {a: (round(covered[a] * 100.0 / total, 2) if total else 0.0) for a in axes}
    with_all = sum(1 for s in servers if all(s in covered_servers[a] for a in axes))

    return {
        "total_servers": total,
        "covered_axes": covered,
        "missing_axes": missing,
        "per_axis_coverage_pct": pct,
        "stale_servers": sorted(stale),
        "servers_with_all_axes": with_all,
    }


class AxisCoverageReport(BaseModel):
    total_servers: int
    covered_axes: Dict[str, int]
    missing_axes: Dict[str, int]
    per_axis_coverage_pct: Dict[str, float]
    stale_servers: List[str]
    servers_with_all_axes: int


def _current_model_version(db: Session) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def load_axis_rows(db: Session, model_version: Optional[str] = None) -> List[dict]:
    """Read the score rows for the current model_version from the app DB."""
    if model_version is None:
        model_version = _current_model_version(db)
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.scored_at,
    )
    if model_version is not None:
        stmt = stmt.where(McpLlmAxisScore.model_version == model_version)
    return [
        {"server_id": sid, "axis_name": axis, "p_top": p_top, "scored_at": scored_at}
        for sid, axis, p_top, scored_at in db.execute(stmt).all()
    ]


@router.get("/coverage/axis-gap", response_model=AxisCoverageReport)
def axis_coverage_gap(db: Session = Depends(get_session)) -> AxisCoverageReport:
    """Per-axis scoring-coverage gap across the scored MCP registry."""
    return AxisCoverageReport(**compute_axis_coverage(load_axis_rows(db)))


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)

    # --- pure-function cases: full server, partial server, stale server ---
    full_rows = [{"server_id": "srv_full", "axis_name": a, "p_top": 0.9, "scored_at": now}
                 for a in CANONICAL_AXES]
    partial_rows = [{"server_id": "srv_partial", "axis_name": a, "p_top": 0.8, "scored_at": now}
                    for a in CANONICAL_AXES
                    if a not in ("network_egress", "exploit_surface")]
    stale_rows = [{"server_id": "srv_stale", "axis_name": a, "p_top": 0.7, "scored_at": old}
                  for a in CANONICAL_AXES]

    full = compute_axis_coverage(full_rows, now=now)
    assert full["total_servers"] == 1, full
    assert full["covered_axes"]["overall_risk"] == 1, full
    assert full["missing_axes"]["overall_risk"] == 0, full
    assert full["per_axis_coverage_pct"]["overall_risk"] == 100.0, full
    assert full["servers_with_all_axes"] == 1, full
    assert full["stale_servers"] == [], full

    partial = compute_axis_coverage(partial_rows, now=now)
    assert partial["total_servers"] == 1, partial
    assert partial["missing_axes"]["network_egress"] == 1, partial
    assert partial["missing_axes"]["exploit_surface"] == 1, partial
    assert partial["servers_with_all_axes"] == 0, partial

    stale = compute_axis_coverage(stale_rows, now=now)
    assert stale["stale_servers"] == ["srv_stale"], stale
    assert stale["servers_with_all_axes"] == 1, stale

    # empty input -> zeros, no crash
    empty = compute_axis_coverage([], now=now)
    assert empty["total_servers"] == 0, empty
    assert empty["per_axis_coverage_pct"]["overall_risk"] == 0.0, empty
    assert empty["stale_servers"] == [], empty

    # --- DB path: real app models over SQLite via dependency override ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    sid = 1
    for _srv, axes in (("db_full", CANONICAL_AXES),
                       ("db_partial", tuple(a for a in CANONICAL_AXES
                                           if a != "network_egress"))):
        for ax in axes:
            s.add(McpLlmAxisScore(id=sid, server_id=_srv, axis_name=ax,
                                  label="HIGH", model_version="v3.0_40974559"))
            sid += 1
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/coverage/axis-gap")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_servers"] == 2, j
    assert j["covered_axes"]["network_egress"] == 1, j
    assert j["missing_axes"]["network_egress"] == 1, j
    assert j["servers_with_all_axes"] == 1, j
    assert set(j["stale_servers"]) == set(), j

    print("PASS")
