"""FastAPI router exposing GET /registry/freshness: a dashboard-ready overview of
registry data freshness across all servers.

Surfaces per-server and aggregate freshness statistics (counts of fresh/stale/never-
scanned/under-assessed servers, median days since scan, oldest server by last_scanned).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, select, func
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from app.read_surface_guard import SharedSnapshot, arm_statement_timeout, timeout_as_503

router = APIRouter(prefix="/registry", tags=["registry"])

# Default query-param triple -- the only shape the public watcher and dashboard
# hit, and the one the shared snapshot precomputes.
_DEFAULT_PARAMS = (24, 7, 30)

# FU-667: this route used to pull EVERY scanned registry row (server_id, name,
# last_scanned) into Python over the full ~477k-server corpus, loop it in
# Python, AND materialise every never-scanned server a second time, plus a
# COUNT(DISTINCT server_id) over the multi-hundred-k score table -- a cold run
# exceeded 45s on the small Fly PG and the route hung (HTTP 000), which is the
# FU-667 outage for /registry/freshness. The counts are now computed IN SQL
# (one grouped pass, no row materialisation), the per-server never-scanned list
# is CAPPED (it is a dashboard hint, not an export), and the whole thing is
# TTL-cached like the sibling /freshness surface -- these aggregates only move
# when a scan/rescore lands. Bounded by a Postgres statement_timeout so a
# pathological plan is cancelled into an honest 503, never a worker hang.
# 60-min TTL vs the 30-min shared-snapshot refresh (weekly-moving aggregates).
CACHE_TTL_SECONDS = 3600
# The never-scanned list is a dashboard preview, not a bulk export (THE LINE:
# no per-server bulk surface). Cap it; the never_scanned COUNT stays exact.
_NEVER_SCANNED_LIST_CAP = 100

_lock = threading.Lock()
# cache key = (fresh_hours, stale_days, very_stale_days) -> (payload, monotonic)
_cache: dict[tuple[int, int, int], tuple["RegistryFreshnessResponse", float]] = {}


class FreshnessBuckets(BaseModel):
    fresh_24h: int = 0
    fresh_7d: int = 0
    stale_7d_30d: int = 0
    stale_over_30d: int = 0


class NeverScannedServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    first_seen: Optional[str] = None


class OldestScan(BaseModel):
    server_id: str
    name: Optional[str] = None
    days_ago: int = 0


class RegistryFreshnessResponse(BaseModel):
    total_servers: int = 0
    scanned_servers: int = 0
    never_scanned: int = 0
    stale_servers: int = 0
    median_days_since_scan: float = 0.0
    oldest_scan: Optional[OldestScan] = None
    freshness_buckets: FreshnessBuckets = FreshnessBuckets()
    never_scanned_servers: list[NeverScannedServer] = []
    assessed_servers: int = 0
    unassessed_servers: int = 0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    mid = n // 2
    if n % 2 == 1:
        return float(sorted_vals[mid])
    return (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0


def _compute(
    db: Session, now: datetime, fresh_hours: int, stale_days: int, very_stale_days: int
) -> RegistryFreshnessResponse:
    """Compute the freshness overview with aggregation pushed INTO SQL.

    One grouped COUNT pass over the registry (no per-row materialisation), a
    separate exact COUNT(DISTINCT server_id) over the score table, a CAPPED
    never-scanned preview list, and a DB-side median. Bounded upstream by the
    session's statement_timeout so a bad plan is cancelled, not hung."""
    fresh_cutoff = now - timedelta(hours=fresh_hours)
    week_cutoff = now - timedelta(days=7)
    month_cutoff = now - timedelta(days=30)

    scanned = McpServerRegistry.last_scanned
    # Bucket counts as conditional SUMs in ONE grouped pass -- portable across
    # sqlite (CI) and Postgres. Buckets match the original Python ladder exactly:
    #   fresh_24h : last_scanned >= fresh_cutoff
    #   fresh_7d  : last_scanned >= (now - 7d)          (includes fresh_24h)
    #   7d..30d   : (now - 30d) <= last_scanned < (now - 7d)
    #   >30d      : last_scanned < (now - 30d)
    def _sum_if(cond):
        return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)

    agg = db.execute(
        select(
            func.count().label("total"),
            _sum_if(scanned.isnot(None)).label("scanned"),
            _sum_if(scanned >= fresh_cutoff).label("fresh_24h"),
            _sum_if(scanned >= week_cutoff).label("fresh_7d"),
            _sum_if((scanned < week_cutoff) & (scanned >= month_cutoff)).label("stale_7d_30d"),
            _sum_if(scanned.isnot(None) & (scanned < month_cutoff)).label("stale_over_30d"),
            func.min(scanned).label("oldest_scanned_at"),
        )
    ).one()

    total_servers = int(agg.total or 0)
    scanned_servers = int(agg.scanned or 0)
    never_scanned = total_servers - scanned_servers

    # Oldest scan: one row, index-backed MIN then a single lookup for its name.
    oldest_scan = None
    oldest_at = agg.oldest_scanned_at
    if oldest_at is not None:
        if oldest_at.tzinfo is None:
            oldest_at = oldest_at.replace(tzinfo=timezone.utc)
        oldest_row = db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name)
            .where(McpServerRegistry.last_scanned == agg.oldest_scanned_at)
            .limit(1)
        ).first()
        if oldest_row:
            oldest_scan = OldestScan(
                server_id=oldest_row[0],
                name=oldest_row[1],
                days_ago=int((now - oldest_at).total_seconds() // 86400),
            )

    # Median days since scan, DB-side. Postgres: percentile_cont over a NUMERIC
    # (days-since-scan), NOT over the timestamp -- percentile_cont has no
    # (fraction) WITHIN GROUP (ORDER BY timestamptz) form, so ordering a
    # timestamp column raises `function percentile_cont(double precision,
    # timestamp with time zone) does not exist` (FU-667, caught in prod warm).
    # Order by epoch-days instead; the result is already the median age in days.
    # sqlite (CI/dev, tiny): fall back to the in-Python median over the small set.
    median_days = 0.0
    if scanned_servers:
        if db.get_bind().dialect.name == "postgresql":
            age_days = func.extract("epoch", func.now() - scanned) / 86400.0
            median_days = db.execute(
                select(func.percentile_cont(0.5).within_group(age_days.asc()))
                .where(scanned.isnot(None))
            ).scalar() or 0.0
        else:
            rows = db.execute(select(scanned).where(scanned.isnot(None))).scalars().all()
            days = []
            for ls in rows:
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                days.append((now - ls).total_seconds() / 86400.0)
            median_days = _median(days)

    # Never-scanned preview list -- CAPPED (dashboard hint, not an export).
    never_scanned_servers = [
        NeverScannedServer(
            server_id=sid,
            name=name,
            first_seen=fs.isoformat() if fs else None,
        )
        for sid, name, fs in db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.first_seen)
            .where(McpServerRegistry.last_scanned.is_(None))
            .limit(_NEVER_SCANNED_LIST_CAP)
        ).all()
    ]

    # Assessed = servers with >=1 axis score. COUNT(DISTINCT server_id) over the
    # 4.7M-row score table forces a sort (~24-56s on prod); COUNT(*) over a
    # GROUP BY server_id hash-aggregates off the unique index (~5s, same count).
    assessed_count = db.execute(
        select(func.count()).select_from(
            select(McpLlmAxisScore.server_id)
            .group_by(McpLlmAxisScore.server_id).subquery())
    ).scalar() or 0
    unassessed_servers = total_servers - assessed_count

    stale_7d_30d = int(agg.stale_7d_30d or 0)
    stale_over_30d = int(agg.stale_over_30d or 0)
    return RegistryFreshnessResponse(
        total_servers=total_servers,
        scanned_servers=scanned_servers,
        never_scanned=never_scanned,
        # Match the original ladder: stale = (7d..30d) + (>30d), on the fixed
        # 7/30-day boundaries (NOT the stale_days query param, which only
        # affected the old per-row loop's unused branch).
        stale_servers=stale_7d_30d + stale_over_30d,
        median_days_since_scan=round(median_days, 2),
        oldest_scan=oldest_scan,
        freshness_buckets=FreshnessBuckets(
            fresh_24h=int(agg.fresh_24h or 0),
            fresh_7d=int(agg.fresh_7d or 0),
            stale_7d_30d=stale_7d_30d,
            stale_over_30d=stale_over_30d,
        ),
        never_scanned_servers=never_scanned_servers,
        assessed_servers=assessed_count,
        unassessed_servers=unassessed_servers,
    )


def _compute_default(db: Session) -> dict:
    """Background-snapshot compute for the default parameter triple -- the shape
    the public watcher and dashboard hit. Returns a plain dict (the snapshot
    stores dicts); `now` is taken at compute time."""
    resp = _compute(db, datetime.now(timezone.utc), *_DEFAULT_PARAMS)
    return resp.model_dump()


# FU-667: the default-params view costs ~50s+ cold on the 1-vCPU prod PG (the
# score-table aggregates). Compute it OFF the request path via a SINGLE elected
# worker (advisory lock) that writes freshness_stats; the route reads that one
# row O(1). Non-default params (rare dashboard tweaks) fall through to a bounded
# request-time compute. advisory_lock_key in the FU-667 667_0xx band.
_snapshot = SharedSnapshot(
    "registry_freshness", _compute_default, SessionLocal, advisory_lock_key=667002,
    ttl_seconds=CACHE_TTL_SECONDS, refresh_interval_seconds=1800)


@router.get("/freshness", response_model=RegistryFreshnessResponse)
def get_registry_freshness(
    fresh_hours: int = Query(default=24, ge=1, le=168),
    stale_days: int = Query(default=7, ge=1, le=90),
    very_stale_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> RegistryFreshnessResponse:
    """Dashboard-ready overview of registry data freshness across all servers.

    The default-params view (the public/watcher shape) is served from the shared
    snapshot (FU-667) -- 200 instantly from freshness_stats, or 503 while
    warming. Custom params are TTL-cached per triple and bounded by a Postgres
    statement_timeout so a cold slow pass is cancelled into an honest 503."""
    params = (fresh_hours, stale_days, very_stale_days)
    if params == _DEFAULT_PARAMS:
        # snapshot returns a dict with cache_age/computed_at metadata; strip the
        # extras the response_model does not declare so validation stays clean.
        snap = _snapshot.get(db)
        return RegistryFreshnessResponse(**{
            k: v for k, v in snap.items()
            if k not in ("cache_age_seconds", "cache_ttl_seconds", "stale", "computed_at")})

    key = params
    now_mono = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit is not None and (now_mono - hit[1]) < CACHE_TTL_SECONDS:
            return hit[0]

    arm_statement_timeout(db)
    try:
        payload = _compute(db, datetime.now(timezone.utc), fresh_hours, stale_days, very_stale_days)
    except Exception as exc:  # noqa: BLE001 -- classify: timeout -> 503, else re-raise
        raise timeout_as_503(exc)

    with _lock:
        _cache[key] = (payload, time.monotonic())
    return payload


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    past_12h = now - timedelta(hours=12)
    past_3d = now - timedelta(days=3)
    past_14d = now - timedelta(days=14)
    past_45d = now - timedelta(days=45)

    s = TS()
    # srv1: fresh (< 24h)
    s.add(McpServerRegistry(server_id="srv1", name="Fresh Server",
                            url="https://example.com/fresh", last_scanned=past_12h,
                            first_seen=past_45d))
    # srv2: fresh (< 7d)
    s.add(McpServerRegistry(server_id="srv2", name="Medium Fresh",
                            url="https://example.com/medium", last_scanned=past_3d,
                            first_seen=past_45d))
    # srv3: stale (7d-30d)
    s.add(McpServerRegistry(server_id="srv3", name="Stale Server",
                            url="https://example.com/stale", last_scanned=past_14d,
                            first_seen=past_45d))
    # srv4: very stale (> 30d)
    s.add(McpServerRegistry(server_id="srv4", name="Very Stale",
                            url="https://example.com/verystale", last_scanned=past_45d,
                            first_seen=past_45d))
    # srv5: never scanned
    s.add(McpServerRegistry(server_id="srv5", name="Never Scanned",
                            url="https://example.com/never", first_seen=past_45d))
    # srv6: never scanned
    s.add(McpServerRegistry(server_id="srv6", name="Also Never",
                            url="https://example.com/alsonever", first_seen=past_45d))
    s.commit()

    # Seed a few axis scores (srv1 assessed, srv5 not)
    for i, (ax, lbl) in enumerate((("overall_risk", "LOW"), ("auth_strength", "STRONG")), start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)

    # The default-params path serves the BACKGROUND snapshot, which computes via
    # the module SessionLocal (not this test's TS) on a daemon. Point it at the
    # test factory and drive ONE synchronous refresh so the GET is deterministic.
    _snapshot._session_factory = TS
    _snapshot.refresh_if_stale(force=True)

    # Test 1: mixed dataset
    r = c.get("/registry/freshness"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["total_servers"] == 6, j
    assert j["scanned_servers"] == 4, j
    assert j["never_scanned"] == 2, j
    assert j["stale_servers"] == 2, j  # srv3 + srv4
    assert j["freshness_buckets"]["fresh_24h"] == 1, j  # srv1
    assert j["freshness_buckets"]["fresh_7d"] == 2, j   # srv1 + srv2
    assert j["freshness_buckets"]["stale_7d_30d"] == 1, j  # srv3
    assert j["freshness_buckets"]["stale_over_30d"] == 1, j  # srv4
    assert j["assessed_servers"] == 1, j  # only srv1 has axis scores
    assert j["unassessed_servers"] == 5, j
    assert len(j["never_scanned_servers"]) == 2, j  # srv5 + srv6
    # Oldest scan should be srv4 (45 days ago)
    assert j["oldest_scan"]["server_id"] == "srv4", j
    # Median of [0.5, 3, 14, 45] days = (3+14)/2 = 8.5
    assert abs(j["median_days_since_scan"] - 8.5) < 0.01, j

    # Cache check: a second call on the SAME params serves the snapshot again
    # (identical body) without a recompute -- the snapshot is live.
    r_again = c.get("/registry/freshness"); assert r_again.status_code == 200
    assert r_again.json() == j, "second call must serve the cached snapshot payload"

    # Test 2: empty registry
    eng2 = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    Base.metadata.create_all(eng2)
    TS2 = sessionmaker(bind=eng2, autoflush=False, autocommit=False)
    app2 = FastAPI(); app2.include_router(router)

    def _override_session2():
        d = TS2()
        try:
            yield d
        finally:
            d.close()

    app2.dependency_overrides[get_session] = _override_session2
    c2 = TestClient(app2)
    # Re-point + re-warm the snapshot at the empty DB (see Test 1 note).
    _snapshot._session_factory = TS2
    _snapshot.refresh_if_stale(force=True)
    r2 = c2.get("/registry/freshness"); assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["total_servers"] == 0, j2
    assert j2["scanned_servers"] == 0, j2
    assert j2["never_scanned"] == 0, j2
    assert j2["assessed_servers"] == 0, j2
    assert j2["median_days_since_scan"] == 0.0, j2

    print("PASS")
