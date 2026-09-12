# deps: fastapi, pydantic, sqlalchemy
"""unscored_backlog_report_api -- report on servers in the registry that have never
been scored, broken down by source and risk tier.

GET /api/scoring/backlog/unscored-report
  Returns servers in mcp_server_registry with no entry in mcp_llm_axis_scores,
  with counts grouped by registry_source and risk_tier, plus sample servers.

GET /api/scoring/backlog/unscored-report/burndown
  Returns a day-by-day cumulative burndown of never-scored backlog over N days.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, distinct
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["unscored_backlog_report_api"])


# --------------------------------------------------------------------------- #
# Request / response shapes
# --------------------------------------------------------------------------- #

class ServerSample(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    url: str | None
    risk_tier: str | None
    first_seen: str | None


class SourceBreakdown(BaseModel):
    registry_source: str
    total: int
    by_risk_tier: List[dict]  # [{risk_tier: str, count: int}]


class UnscoredReportResponse(BaseModel):
    as_of: str
    total_never_scored: int
    total_registry: int
    by_source: List[SourceBreakdown]
    sample_servers: List[ServerSample]


class BurndownPoint(BaseModel):
    date: str
    total_registry: int
    never_scored: int
    scored: int


class UnscoredBurndownResponse(BaseModel):
    as_of: str
    days: int
    total_never_scored: int
    total_scored: int
    total_registry: int
    series: List[BurndownPoint]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/scoring/backlog/unscored-report", response_model=UnscoredReportResponse)
def unscored_report(
    sample_limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_session),
) -> UnscoredReportResponse:
    """
    Return servers in the registry that have never been scored (no row in
    mcp_llm_axis_scores), broken down by registry_source and risk_tier.
    """
    now = datetime.now(timezone.utc)

    # Total registry count
    total_registry: int = (
        db.execute(select(func.count(McpServerRegistry.server_id)))
        .scalar_one()
    ) or 0

    # Sub-query: server_ids that appear in mcp_llm_axis_scores
    scored_subq = (
        select(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )

    total_never_scored: int = (
        db.execute(
            select(func.count(McpServerRegistry.server_id))
            .where(~McpServerRegistry.server_id.in_(scored_subq))
        )
        .scalar_one()
    ) or 0

    never_scored_rows = (
        db.execute(
            select(McpServerRegistry)
            .where(~McpServerRegistry.server_id.in_(scored_subq))
        )
        .scalars()
        .all()
    )

    # Group by registry_source -> risk_tier
    by_source_map: dict[str, dict[str, int]] = {}
    for srv in never_scored_rows:
        src = srv.registry_source or "UNKNOWN"
        tier = srv.risk_tier or "UNKNOWN"
        by_source_map.setdefault(src, {})
        by_source_map[src][tier] = by_source_map[src].get(tier, 0) + 1

    by_source: List[SourceBreakdown] = []
    for src, tiers in sorted(by_source_map.items()):
        total_for_src = sum(tiers.values())
        by_risk_tier = [
            {"risk_tier": t, "count": c}
            for t, c in sorted(tiers.items())
        ]
        by_source.append(SourceBreakdown(
            registry_source=src,
            total=total_for_src,
            by_risk_tier=by_risk_tier,
        ))

    # Sample servers (order by first_seen desc)
    sample_servers = [
        ServerSample(
            server_id=srv.server_id,
            name=srv.name,
            registry_source=srv.registry_source,
            url=srv.url,
            risk_tier=srv.risk_tier,
            first_seen=srv.first_seen.isoformat() if srv.first_seen else None,
        )
        for srv in sorted(
            never_scored_rows,
            key=lambda x: x.first_seen or datetime.min,
            reverse=True,
        )[:sample_limit]
    ]

    return UnscoredReportResponse(
        as_of=now.isoformat(),
        total_never_scored=total_never_scored,
        total_registry=total_registry,
        by_source=by_source,
        sample_servers=sample_servers,
    )


@router.get("/scoring/backlog/unscored-report/burndown", response_model=UnscoredBurndownResponse)
def unscored_burndown(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> UnscoredBurndownResponse:
    """
    Return a day-by-day burndown of never-scored servers over the last N days.
    """
    now = datetime.now(timezone.utc)

    # All server_ids that have at least one axis score
    scored_ids: set[str] = set(
        row[0]
        for row in db.execute(
            select(distinct(McpLlmAxisScore.server_id))
        ).all()
    )

    all_servers = (
        db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.first_seen)
            .where(McpServerRegistry.first_seen.isnot(None))
            .order_by(McpServerRegistry.first_seen)
        )
        .all()
    )

    if not all_servers:
        return UnscoredBurndownResponse(
            as_of=now.isoformat(),
            days=days,
            total_never_scored=0,
            total_scored=0,
            total_registry=0,
            series=[],
        )

    # Earliest scored_at per server
    scored_earliest: dict[str, datetime] = {}
    for server_id, min_scored_at in (
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                func.min(McpLlmAxisScore.scored_at),
            ).group_by(McpLlmAxisScore.server_id)
        ).all()
    ):
        if min_scored_at:
            scored_earliest[server_id] = min_scored_at

    today = now.date()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    dates_set = set(dates)

    daily: dict[str, dict] = {}
    cumulative_registry = 0
    cumulative_scored = 0
    cumulative_never_scored = 0

    for date_str in dates:
        arrivals = [
            (sid, fs)
            for sid, fs in all_servers
            if fs and fs.date().isoformat() == date_str
        ]
        newly_scored = [
            sid for sid, sa in scored_earliest.items()
            if sa and sa.date().isoformat() == date_str
        ]

        cumulative_registry += len(arrivals)
        for sid, _ in arrivals:
            if sid not in scored_ids:
                cumulative_never_scored += 1
        cumulative_scored += len(newly_scored)
        cumulative_never_scored -= len(newly_scored)

        if date_str in dates_set:
            daily[date_str] = {
                "date": date_str,
                "total_registry": cumulative_registry,
                "never_scored": cumulative_never_scored,
                "scored": cumulative_scored,
            }

    series = [BurndownPoint(**daily[d]) for d in sorted(daily) if d in daily]

    return UnscoredBurndownResponse(
        as_of=now.isoformat(),
        days=days,
        total_never_scored=cumulative_never_scored,
        total_scored=cumulative_scored,
        total_registry=cumulative_registry,
        series=series,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys, pathlib
    # Ensure repo root (parent of app/) is on sys.path so `app.db` resolves
    _root = pathlib.Path(__file__).resolve().parents[2]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    sys.path.insert(0, "/home/workspace/zo_sentinel")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(512),
                registry_source VARCHAR(64),
                url TEXT,
                description TEXT,
                trust_score FLOAT,
                verdict VARCHAR(64),
                verdict_reasoning TEXT,
                confidence FLOAT,
                last_assessed TIMESTAMP,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                risk_tier VARCHAR(32),
                metadata TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128),
                axis_name VARCHAR(64),
                label VARCHAR(64),
                label_index INTEGER,
                probs TEXT,
                p_top FLOAT,
                p_critical FLOAT,
                p_danger FLOAT,
                escalated BOOLEAN,
                escalated_to VARCHAR(32),
                decision_rule_version VARCHAR(32),
                model_version VARCHAR(64),
                adapter_sha256 VARCHAR(80),
                scored_at TIMESTAMP
            )
        """))
        conn.commit()

    today = datetime.now(timezone.utc).date()

    with engine.connect() as conn:
        rows = [
            ("srv1", "Alpha",   "npm",   "HIGH",   (today - timedelta(days=5)).isoformat()),
            ("srv2", "Beta",    "npm",   "HIGH",   (today - timedelta(days=5)).isoformat()),
            ("srv3", "Gamma",   "npm",   "MEDIUM", (today - timedelta(days=4)).isoformat()),
            ("srv4", "Delta",   "github","LOW",    (today - timedelta(days=2)).isoformat()),
            ("srv5", "Epsilon", "github","LOW",    (today - timedelta(days=2)).isoformat()),
        ]
        for sid, name, src, tier, fs in rows:
            conn.execute(text(
                "INSERT INTO mcp_server_registry "
                "(server_id, name, registry_source, risk_tier, url, first_seen, last_seen, scan_count, confidence) "
                "VALUES (:sid, :name, :src, :tier, :url, :fs, NULL, 0, 0.5)"
            ), {"sid": sid, "name": name, "src": src, "tier": tier,
                "url": f"https://example.com/{sid}", "fs": fs})
        # srv1 and srv3 are scored; srv2, srv4, srv5 are never-scored
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, model_version, label, scored_at) "
            "VALUES ('srv1', 'overall_risk', 'v1', 'MEDIUM', :ts)"
        ), {"ts": f"{(today - timedelta(days=5)).isoformat()} 00:00:00"})
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, model_version, label, scored_at) "
            "VALUES ('srv3', 'overall_risk', 'v1', 'LOW', :ts)"
        ), {"ts": f"{(today - timedelta(days=4)).isoformat()} 00:00:00"})
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    # Test 1: unscored report
    resp = client.get("/api/scoring/backlog/unscored-report?sample_limit=5")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["total_registry"] == 5, f"total_registry: expected 5, got {data['total_registry']}"
    assert data["total_never_scored"] == 3, f"total_never_scored: expected 3, got {data['total_never_scored']}"

    by_src = {s["registry_source"]: s for s in data["by_source"]}
    assert "npm" in by_src, f"'npm' missing from by_source: {data['by_source']}"
    assert by_src["npm"]["total"] == 2, f"npm total expected 2, got {by_src['npm']['total']}"
    assert "github" in by_src, f"'github' missing from by_source: {data['by_source']}"
    assert by_src["github"]["total"] == 1, f"github total expected 1, got {by_src['github']['total']}"

    sample_ids = {s["server_id"] for s in data["sample_servers"]}
    assert sample_ids == {"srv2", "srv4", "srv5"}, f"sample mismatch: {sample_ids}"

    # Test 2: burndown
    resp2 = client.get("/api/scoring/backlog/unscored-report/burndown?days=5")
    assert resp2.status_code == 200, resp2.text
    bdata = resp2.json()

    assert bdata["total_registry"] == 5, f"burndown total_registry: expected 5, got {bdata['total_registry']}"
    assert bdata["total_scored"] == 2, f"burndown total_scored: expected 2, got {bdata['total_scored']}"
    assert bdata["total_never_scored"] == 3, f"burndown total_never_scored: expected 3, got {bdata['total_never_scored']}"
    assert len(bdata["series"]) == 5, f"expected 5 daily rows, got {len(bdata['series'])}"

    print("PASS")
    sys.exit(0)
