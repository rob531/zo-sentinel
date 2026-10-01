# deps: fastapi, sqlalchemy, pydantic
"""Scoring Wave History Service.

GET /api/scoring_wave_history
  Returns chronological list of scoring waves with per-wave and per-server details,
  drawn from mcp_llm_axis_scores + mcp_server_registry.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy ORM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["scoring_wave_history"])


# --------------------------------------------------------------------------- #
# Pydantic request/response models
# --------------------------------------------------------------------------- #


class AxisScoreEntry(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    scored_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ServerWaveDetail(BaseModel):
    server_id: str
    name: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    axis_scores: list[AxisScoreEntry] = Field(default_factory=list)


class WaveEntry(BaseModel):
    wave_id: str
    wave_start: datetime
    wave_end: datetime
    servers_scored: int
    total_scores: int
    model_version: Optional[str] = None
    newest_score_at: Optional[datetime] = None
    oldest_score_at: Optional[datetime] = None
    risk_tier_breakdown: dict[str, int] = Field(default_factory=dict)


class WaveDetail(BaseModel):
    wave_id: str
    wave_start: datetime
    wave_end: datetime
    model_version: Optional[str] = None
    servers: list[ServerWaveDetail] = Field(default_factory=list)
    newest_score_at: Optional[datetime] = None
    oldest_score_at: Optional[datetime] = None


class ServerHistoryEntry(BaseModel):
    wave_id: str
    wave_start: datetime
    wave_end: datetime
    model_version: Optional[str] = None
    axis_scores: list[AxisScoreEntry] = Field(default_factory=list)
    p_top: Optional[float] = None
    label: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ServerHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str] = None
    waves: list[ServerHistoryEntry] = Field(default_factory=list)


class WaveHistoryListResponse(BaseModel):
    wave_window_hours: int
    waves: list[WaveEntry] = Field(default_factory=list)
    total: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _wave_slot(ts: datetime, wave_hours: int) -> datetime:
    """Return the UTC start of the wave window containing ts."""
    total_minutes = ts.hour * 60 + ts.minute + ts.second / 60.0
    wave_slot = int(total_minutes // (wave_hours * 60))
    wave_start_minute = wave_slot * wave_hours * 60
    wave_start_hour = wave_start_minute // 60
    wave_start_min = wave_start_minute % 60
    return ts.replace(hour=wave_start_hour, minute=wave_start_min, second=0, microsecond=0)


def _group_rows_into_waves(
    rows: list,
    wave_hours: int,
) -> dict[tuple, dict]:
    """Group scored rows into wave buckets keyed by (wave_start, wave_end)."""
    wave_map: dict[tuple, dict] = {}
    for row in rows:
        ts = row.scored_at or datetime.now(timezone.utc)
        ws = _wave_slot(ts, wave_hours)
        we = ws + timedelta(hours=wave_hours)
        key = (ws, we)
        if key not in wave_map:
            wave_map[key] = {
                "servers": {},
                "all_scores": [],
                "model_version": None,
                "newest": None,
                "oldest": None,
            }
        wm = wave_map[key]
        wm["all_scores"].append(row)
        if row.server_id not in wm["servers"]:
            wm["servers"][row.server_id] = []
        wm["servers"][row.server_id].append(row)
        if wm["model_version"] is None:
            wm["model_version"] = row.model_version
        if wm["newest"] is None or (row.scored_at and row.scored_at > wm["newest"]):
            wm["newest"] = row.scored_at
        if wm["oldest"] is None or (row.scored_at and row.scored_at < wm["oldest"]):
            wm["oldest"] = row.scored_at
    return wave_map


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get(
    "/scoring_wave_history",
    response_model=WaveHistoryListResponse,
    summary="List all scoring waves with summary stats",
)
def list_wave_history(
    wave_window_hours: int = Query(default=6, ge=1, le=72, description="Wave window size in hours"),
    days: int = Query(default=30, ge=1, le=365, description="Look-back window in days"),
    limit: int = Query(default=20, ge=1, le=200, description="Max waves to return"),
    db: Session = Depends(get_session),
) -> WaveHistoryListResponse:
    """
    Return chronological list of scoring waves, newest first.
    Each wave covers `wave_window_hours` duration.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.label,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.p_danger,
                McpLlmAxisScore.model_version,
                McpLlmAxisScore.scored_at,
            )
            .where(McpLlmAxisScore.scored_at >= cutoff)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(limit * 200)
        )
        .all()
    )

    wave_map = _group_rows_into_waves(rows, wave_window_hours)
    sorted_keys = sorted(wave_map.keys(), reverse=True)[:limit]

    wave_entries: list[WaveEntry] = []
    for (ws, we) in sorted_keys:
        wm = wave_map[(ws, we)]
        # tier breakdown
        sids = list(wm["servers"].keys())
        tier_rows = (
            db.execute(
                select(McpServerRegistry.server_id, McpServerRegistry.risk_tier)
                .where(McpServerRegistry.server_id.in_(sids))
            )
            .all()
        )
        tier_map = {r.server_id: r.risk_tier for r in tier_rows}
        breakdown: dict[str, int] = {}
        for sid in sids:
            tier = tier_map.get(sid) or "UNKNOWN"
            breakdown[tier] = breakdown.get(tier, 0) + 1

        wave_entries.append(
            WaveEntry(
                wave_id=ws.strftime("%Y%m%d%H%M"),
                wave_start=ws,
                wave_end=we,
                servers_scored=len(sids),
                total_scores=len(wm["all_scores"]),
                model_version=wm["model_version"],
                newest_score_at=wm["newest"],
                oldest_score_at=wm["oldest"],
                risk_tier_breakdown=breakdown,
            )
        )

    return WaveHistoryListResponse(
        wave_window_hours=wave_window_hours,
        waves=wave_entries,
        total=len(wave_entries),
    )


@router.get(
    "/scoring_wave_history/{wave_id}",
    response_model=WaveDetail,
    summary="Get full details of a specific scoring wave",
)
def get_wave_detail(
    wave_id: str,
    wave_window_hours: int = Query(default=6, ge=1, le=72),
    db: Session = Depends(get_session),
) -> WaveDetail:
    """
    Given a wave_id like '202508210000', return all servers and their
    axis scores within that wave window.
    """
    try:
        wave_dt = datetime.strptime(wave_id, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid wave_id format: {wave_id}")

    ws = wave_dt
    we = ws + timedelta(hours=wave_window_hours)

    rows = (
        db.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.label,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.p_danger,
                McpLlmAxisScore.model_version,
                McpLlmAxisScore.scored_at,
            )
            .where(McpLlmAxisScore.scored_at >= ws)
            .where(McpLlmAxisScore.scored_at < we)
        )
        .all()
    )

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scoring data found for wave {wave_id}")

    wave_map = _group_rows_into_waves(rows, wave_window_hours)
    if not wave_map:
        raise HTTPException(status_code=404, detail=f"No scoring data found for wave {wave_id}")

    (ws_key, we_key), wm = next(iter(wave_map.items()))
    sids = list(wm["servers"].keys())

    server_info_rows = (
        db.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.risk_tier,
                McpServerRegistry.verdict,
            )
            .where(McpServerRegistry.server_id.in_(sids))
        )
        .all()
    )
    info_map = {r.server_id: r for r in server_info_rows}

    servers_out: list[ServerWaveDetail] = []
    for sid, score_rows in wm["servers"].items():
        info = info_map.get(sid)
        axis_list = [
            AxisScoreEntry(
                axis_name=r.axis_name,
                label=r.label,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                scored_at=r.scored_at,
            )
            for r in sorted(score_rows, key=lambda x: x.axis_name)
        ]
        servers_out.append(
            ServerWaveDetail(
                server_id=sid,
                name=info.name if info else None,
                risk_tier=info.risk_tier if info else None,
                verdict=info.verdict if info else None,
                axis_scores=axis_list,
            )
        )

    return WaveDetail(
        wave_id=wave_id,
        wave_start=ws_key,
        wave_end=we_key,
        model_version=wm["model_version"],
        servers=servers_out,
        newest_score_at=wm["newest"],
        oldest_score_at=wm["oldest"],
    )


@router.get(
    "/scoring_wave_history/server/{server_id}",
    response_model=ServerHistoryResponse,
    summary="Get scoring history for a specific server across all waves",
)
def get_server_wave_history(
    server_id: str,
    wave_window_hours: int = Query(default=6, ge=1, le=72),
    days: int = Query(default=90, ge=1, le=365, description="Look-back window in days"),
    db: Session = Depends(get_session),
) -> ServerHistoryResponse:
    """
    Return all scoring waves this server participated in, newest first.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.execute(
            select(
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.label,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.p_danger,
                McpLlmAxisScore.model_version,
                McpLlmAxisScore.scored_at,
            )
            .where(McpLlmAxisScore.server_id == server_id)
            .where(McpLlmAxisScore.scored_at >= cutoff)
            .order_by(McpLlmAxisScore.scored_at.desc())
        )
        .all()
    )

    if not rows:
        raise HTTPException(status_code=404, detail=f"No scoring history found for server {server_id}")

    # Group by wave
    wave_map: dict[tuple, list] = {}
    for row in rows:
        ts = row.scored_at or datetime.now(timezone.utc)
        ws = _wave_slot(ts, wave_window_hours)
        we = ws + timedelta(hours=wave_window_hours)
        key = (ws, we)
        if key not in wave_map:
            wave_map[key] = []
        wave_map[key].append(row)

    # Look up server name
    info_row = (
        db.execute(
            select(McpServerRegistry.name).where(McpServerRegistry.server_id == server_id)
        )
        .scalar_one_or_none()
    )

    sorted_keys = sorted(wave_map.keys(), reverse=True)
    wave_entries: list[ServerHistoryEntry] = []
    for (ws, we) in sorted_keys:
        score_rows = wave_map[(ws, we)]
        axis_list = [
            AxisScoreEntry(
                axis_name=r.axis_name,
                label=r.label,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                scored_at=r.scored_at,
            )
            for r in sorted(score_rows, key=lambda x: x.axis_name)
        ]
        overall_row = next((r for r in score_rows if r.axis_name == "overall_risk"), score_rows[0])
        wave_entries.append(
            ServerHistoryEntry(
                wave_id=ws.strftime("%Y%m%d%H%M"),
                wave_start=ws,
                wave_end=we,
                model_version=overall_row.model_version,
                axis_scores=axis_list,
                p_top=overall_row.p_top,
                label=overall_row.label,
            )
        )

    return ServerHistoryResponse(
        server_id=server_id,
        name=info_row,
        waves=wave_entries,
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
    _SessionLocal = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    now = datetime.now(timezone.utc)
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with _SessionLocal() as db:
        # 2 servers
        db.add(McpServerRegistry(
            server_id="srv1", name="Server One", registry_source="test",
            risk_tier="LOW", verdict="CLEAN",
        ))
        db.add(McpServerRegistry(
            server_id="srv2", name="Server Two", registry_source="test",
            risk_tier="HIGH", verdict="REVIEW",
        ))
        db.flush()

        # Wave 1: srv1 scored at t=2h, t=3h (same 6h wave window)
        for offset_h in (2, 3):
            db.add(McpLlmAxisScore(
                server_id="srv1",
                axis_name="overall_risk",
                label="LOW",
                p_top=0.9,
                p_critical=0.05,
                p_danger=0.1,
                model_version="v1",
                scored_at=base + timedelta(hours=offset_h),
                adapter_sha256="a" * 64,
                decision_rule_version="v1",
                probs={},
                escalated=False,
                label_index=0,
            ))
        # Wave 2: srv1 at t=8h (new wave), srv2 at t=8h
        db.add(McpLlmAxisScore(
            server_id="srv1",
            axis_name="overall_risk",
            label="LOW",
            p_top=0.85,
            p_critical=0.06,
            p_danger=0.12,
            model_version="v1",
            scored_at=base + timedelta(hours=8),
            adapter_sha256="b" * 64,
            decision_rule_version="v1",
            probs={},
            escalated=False,
            label_index=0,
        ))
        db.add(McpLlmAxisScore(
            server_id="srv2",
            axis_name="overall_risk",
            label="HIGH",
            p_top=0.3,
            p_critical=0.5,
            p_danger=0.7,
            model_version="v1",
            scored_at=base + timedelta(hours=8),
            adapter_sha256="c" * 64,
            decision_rule_version="v1",
            probs={},
            escalated=False,
            label_index=2,
        ))
        db.commit()

    def _override():
        sess = _SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    from fastapi import FastAPI

    _app = FastAPI()
    _app.include_router(router)
    _app.dependency_overrides[get_session] = _override

    _client = TestClient(_app)

    # Test 1: list waves
    r = _client.get("/api/scoring_wave_history?wave_window_hours=6&days=30&limit=10")
    assert r.status_code == 200, f"list waves: {r.status_code} {r.text}"
    d = r.json()
    assert "waves" in d, f"no waves key: {d}"
    assert d["wave_window_hours"] == 6
    # 2 waves expected
    assert len(d["waves"]) == 2, f"Expected 2 waves, got {len(d['waves'])}: {[w['wave_id'] for w in d['waves']]}"

    # Wave 1 (newest): srv1 + srv2
    w_newest = d["waves"][0]
    assert w_newest["servers_scored"] == 2, f"w_newest servers: {w_newest['servers_scored']}"
    assert w_newest["total_scores"] == 2, f"w_newest scores: {w_newest['total_scores']}"
    assert w_newest["risk_tier_breakdown"].get("HIGH", 0) == 1
    assert w_newest["risk_tier_breakdown"].get("LOW", 0) == 1

    # Wave 2 (older): srv1 only
    w_older = d["waves"][1]
    assert w_older["servers_scored"] == 1
    assert w_older["risk_tier_breakdown"].get("LOW", 0) == 1

    # Test 2: get wave detail
    wave_id = w_newest["wave_id"]
    r2 = _client.get(f"/api/scoring_wave_history/{wave_id}?wave_window_hours=6")
    assert r2.status_code == 200, f"wave detail: {r2.status_code} {r2.text}"
    d2 = r2.json()
    assert len(d2["servers"]) == 2, f"Expected 2 servers in wave, got {len(d2['servers'])}"
    srv_ids = {s["server_id"] for s in d2["servers"]}
    assert srv_ids == {"srv1", "srv2"}, f"srv_ids: {srv_ids}"

    # Test 3: server history
    r3 = _client.get("/api/scoring_wave_history/server/srv1?wave_window_hours=6&days=30")
    assert r3.status_code == 200, f"server history: {r3.status_code} {r3.text}"
    d3 = r3.json()
    assert d3["server_id"] == "srv1"
    assert d3["name"] == "Server One"
    assert len(d3["waves"]) == 2, f"Expected 2 waves for srv1, got {len(d3['waves'])}"

    # Test 4: server history 404 for unknown
    r4 = _client.get("/api/scoring_wave_history/server/nonexistent?wave_window_hours=6")
    assert r4.status_code == 404, f"unknown server should 404, got {r4.status_code}"

    # Test 5: invalid wave_id
    r5 = _client.get("/api/scoring_wave_history/notvalid?wave_window_hours=6")
    assert r5.status_code == 400, f"invalid wave_id should 400, got {r5.status_code}"

    print("PASS")
    sys.exit(0)
