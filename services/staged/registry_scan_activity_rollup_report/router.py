"""registry_scan_activity_rollup_report.py -- registry scan-activity rollup report.

Reads the app-tier registry (app.db / app.models.McpServerRegistry) and rolls up
how active scanning has been over the last N days: how many servers were SEEN,
how many were SCANNED, how many are STALE, which registry sources contribute,
and how the seen population splits across published risk tiers.

Published risk tiers go through trust_gating_override.trust_gate so official
publishers (Stripe / Microsoft / Google ...) are not reported as false
HIGH/CRITICAL -- the same gate the verdict endpoint uses.

Read-only: no writes to any table, no network calls, no duckdb.

Interface:
    build_rollup(days: int = 7) -> dict
    keys: generated_at, days, servers_seen, servers_scanned, distinct_sources,
          scan_count_total, avg_scan_count, stale_servers, buckets[{band, count}]

Run:  python3 registry_scan_activity_rollup_report.py
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session, SessionLocal
from app.models import McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["reports"])

RISK_BANDS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
UNKNOWN_BAND = "UNKNOWN"


def _utcnow() -> datetime:
    """Naive UTC -- registry timestamps are stored as UTC (tz-naive on sqlite)."""
    return datetime.utcnow()


def _naive(value: Any) -> Optional[datetime]:
    """Normalise a DB timestamp to naive UTC so comparisons never mix tz-aware
    and tz-naive datetimes (Postgres returns aware, sqlite returns naive)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(tz=None).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _published_band(server: Any) -> str:
    """Published risk band for a server, after the trust gate.

    The registry row carries its own risk_tier; trust_gate re-calibrates it so
    a verified publisher is never reported above its published cap.
    """
    original = (getattr(server, "risk_tier", None) or "").strip().upper()
    gate = trust_gate(getattr(server, "url", None), getattr(server, "name", None),
                      {"overall_risk": original})
    published = (gate.get("published_overall_risk") or "").strip().upper()
    if published in RISK_BANDS:
        return published
    return UNKNOWN_BAND


def _rollup(rows: List[Any], days: int, cutoff: datetime, generated_at: datetime) -> dict:
    """Pure aggregation over registry rows -> the rollup dict."""
    seen = []
    scanned = 0
    sources = set()
    scan_total = 0
    stale = 0
    bands: Dict[str, int] = {band: 0 for band in RISK_BANDS}
    bands[UNKNOWN_BAND] = 0

    for row in rows:
        last_seen = _naive(getattr(row, "last_seen", None))
        last_scanned = _naive(getattr(row, "last_scanned", None))
        if last_seen is None or last_seen < cutoff:
            continue

        seen.append(row)
        src = getattr(row, "registry_source", None)
        if src:
            sources.add(str(src))
        scan_count = getattr(row, "scan_count", None) or 0
        scan_total += int(scan_count)
        if last_scanned is not None and last_scanned >= cutoff:
            scanned += 1
        else:
            stale += 1
        bands[_published_band(row)] += 1

    avg = float(scan_total) / len(seen) if seen else 0.0
    buckets = [{"band": band, "count": bands[band]} for band in RISK_BANDS]
    if bands[UNKNOWN_BAND]:
        buckets.append({"band": UNKNOWN_BAND, "count": bands[UNKNOWN_BAND]})

    return {
        "generated_at": generated_at.isoformat() + "Z",
        "days": days,
        "servers_seen": len(seen),
        "servers_scanned": scanned,
        "distinct_sources": len(sources),
        "scan_count_total": scan_total,
        "avg_scan_count": avg,
        "stale_servers": stale,
        "buckets": buckets,
    }


def build_rollup(days: int = 7, db: Optional[Session] = None) -> dict:
    """Roll up registry scan activity over the last `days` days.

    `db` is an app DB session (app.db.get_session); when omitted the module's
    own app session is opened and closed here. Read-only.
    """
    try:
        window = max(1, int(days))
    except (TypeError, ValueError):
        window = 7

    generated_at = _utcnow()
    cutoff = generated_at - timedelta(days=window)

    if db is not None:
        rows = db.execute(select(McpServerRegistry)).scalars().all()
        return _rollup(list(rows), window, cutoff, generated_at)

    session = SessionLocal()
    try:
        rows = session.execute(select(McpServerRegistry)).scalars().all()
        return _rollup(list(rows), window, cutoff, generated_at)
    finally:
        session.close()


@router.get("/reports/registry-scan-activity")
def registry_scan_activity(days: int = 7, db: Session = Depends(get_session)) -> dict:
    """Registry scan-activity rollup over the last `days` days."""
    return build_rollup(days=days, db=db)


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
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
    s = TS()
    now = _utcnow()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            registry_source="npm", risk_tier="HIGH", scan_count=3,
                            last_seen=now - timedelta(days=1),
                            last_scanned=now - timedelta(days=1)))
    s.add(McpServerRegistry(server_id="srv2", name="Community MCP",
                            url="https://example.com/community/mcp",
                            registry_source="github", risk_tier="LOW", scan_count=0,
                            last_seen=now - timedelta(days=2), last_scanned=None))
    s.add(McpServerRegistry(server_id="srv3", name="Old MCP",
                            url="https://example.com/old/mcp",
                            registry_source="npm", risk_tier="CRITICAL", scan_count=10,
                            last_seen=now - timedelta(days=30),
                            last_scanned=now - timedelta(days=30)))
    s.commit()

    roll = build_rollup(days=7, db=s)
    expected_keys = {"generated_at", "days", "servers_seen", "servers_scanned",
                     "distinct_sources", "scan_count_total", "avg_scan_count",
                     "stale_servers", "buckets"}
    assert set(roll) == expected_keys, roll
    assert roll["days"] == 7, roll
    assert roll["servers_seen"] == 2, roll          # srv3 is outside the window
    assert roll["servers_scanned"] == 1, roll       # srv2 has never been scanned
    assert roll["distinct_sources"] == 2, roll
    assert roll["scan_count_total"] == 3, roll
    assert isinstance(roll["avg_scan_count"], float), roll
    assert roll["avg_scan_count"] == 1.5, roll
    assert roll["stale_servers"] == 1, roll
    band_counts = {b["band"]: b["count"] for b in roll["buckets"]}
    assert band_counts["MEDIUM"] == 1, roll         # Stripe capped by trust gate
    assert band_counts["LOW"] == 1, roll
    assert band_counts["HIGH"] == 0, roll

    wide = build_rollup(days=365, db=s)
    assert wide["servers_seen"] == 3 and wide["days"] == 365, wide   # srv3 back in window

    narrow = build_rollup(days=1, db=s)
    assert narrow["servers_seen"] == 0 and narrow["days"] == 1, narrow  # nothing that fresh
    assert narrow["avg_scan_count"] == 0.0, narrow

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
    r = c.get("/api/reports/registry-scan-activity?days=7")
    assert r.status_code == 200, r.text
    j = r.json()
    assert set(j) == expected_keys, j
    assert j["servers_seen"] == 2, j
    s.close()

    print(json.dumps(roll, indent=2, sort_keys=True))
    print("PASS")
