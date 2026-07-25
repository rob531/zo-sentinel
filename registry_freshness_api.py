"""FastAPI router exposing GET /registry/freshness: a dashboard-ready overview of
registry data freshness across all servers.

Surfaces per-server and aggregate freshness statistics (counts of fresh/stale/never-
scanned/under-assessed servers, median days since scan, oldest server by last_scanned).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, distinct

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/registry", tags=["registry"])


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


@router.get("/freshness", response_model=RegistryFreshnessResponse)
def get_registry_freshness(
    fresh_hours: int = Query(default=24, ge=1, le=168),
    stale_days: int = Query(default=7, ge=1, le=90),
    very_stale_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> RegistryFreshnessResponse:
    """Dashboard-ready overview of registry data freshness across all servers."""
    now = datetime.now(timezone.utc)
    fresh_cutoff = now - timedelta(hours=fresh_hours)
    stale_cutoff = now - timedelta(days=stale_days)
    very_stale_cutoff = now - timedelta(days=very_stale_days)

    # Total servers in registry
    total_servers = db.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar() or 0

    # Servers that have been scanned at least once (last_scanned IS NOT NULL)
    scanned_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.last_scanned)
        .where(McpServerRegistry.last_scanned.isnot(None))
    ).all()

    scanned_servers = len(scanned_rows)
    never_scanned = total_servers - scanned_servers

    # Build freshness buckets
    fresh_24h = 0
    fresh_7d = 0
    stale_7d_30d = 0
    stale_over_30d = 0
    days_since_scans: list[float] = []
    oldest_row = None
    oldest_days_ago = -1

    for sid, name, last_scanned in scanned_rows:
        if last_scanned is None:
            continue
        # Normalize to UTC-aware
        if last_scanned.tzinfo is None:
            last_scanned = last_scanned.replace(tzinfo=timezone.utc)
        delta = now - last_scanned
        days = delta.total_seconds() / 86400.0
        days_since_scans.append(days)

        if last_scanned >= fresh_cutoff:
            fresh_24h += 1
            fresh_7d += 1
        elif delta <= timedelta(days=7):
            fresh_7d += 1
        elif delta <= timedelta(days=30):
            stale_7d_30d += 1
        else:
            stale_over_30d += 1

        if days > oldest_days_ago:
            oldest_days_ago = days
            oldest_row = (sid, name)

    # Stale servers (last_scanned > stale_days ago)
    stale_servers = stale_7d_30d + stale_over_30d

    # Median days since scan
    median_days = _median(days_since_scans)

    # Oldest scan
    oldest_scan = None
    if oldest_row:
        oldest_scan = OldestScan(
            server_id=oldest_row[0],
            name=oldest_row[1],
            days_ago=int(oldest_days_ago),
        )

    # Never-scanned servers list
    never_scanned_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.first_seen)
        .where(McpServerRegistry.last_scanned.is_(None))
    ).all()
    never_scanned_servers = [
        NeverScannedServer(
            server_id=sid,
            name=name,
            first_seen=fs.isoformat() if fs else None,
        )
        for sid, name, fs in never_scanned_rows
    ]

    # Assessed vs unassessed (have at least 1 row in mcp_llm_axis_scores)
    assessed_count = db.execute(
        select(func.count(distinct(McpLlmAxisScore.server_id)))
    ).scalar() or 0
    unassessed_servers = total_servers - assessed_count

    return RegistryFreshnessResponse(
        total_servers=total_servers,
        scanned_servers=scanned_servers,
        never_scanned=never_scanned,
        stale_servers=stale_servers,
        median_days_since_scan=round(median_days, 2),
        oldest_scan=oldest_scan,
        freshness_buckets=FreshnessBuckets(
            fresh_24h=fresh_24h,
            fresh_7d=fresh_7d,
            stale_7d_30d=stale_7d_30d,
            stale_over_30d=stale_over_30d,
        ),
        never_scanned_servers=never_scanned_servers,
        assessed_servers=assessed_count,
        unassessed_servers=unassessed_servers,
    )


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
    r2 = c2.get("/registry/freshness"); assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["total_servers"] == 0, j2
    assert j2["scanned_servers"] == 0, j2
    assert j2["never_scanned"] == 0, j2
    assert j2["assessed_servers"] == 0, j2
    assert j2["median_days_since_scan"] == 0.0, j2

    print("PASS")
