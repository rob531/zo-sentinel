# deps: fastapi, pydantic, requests
"""FastAPI router for server-freshness metrics.

Returns aggregate freshness metrics across all servers:
- distribution of scan/score freshness ages
- percentile statistics
- staleness breakdown counts
- per-source freshness rollup

Queries the app DB (McpServerRegistry, McpLlmAxisScore).
Public access, no auth required.
"""
from __future__ import annotations

import os
import sys as _sys

_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo not in _sys.path:
    _sys.path.insert(0, _repo)

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_freshness_metrics"])

# Thresholds in seconds (7 days)
_SCAN_STALE_SEC = 7 * 24 * 3600
_ASSESS_STALE_SEC = 7 * 24 * 3600


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class FreshnessPercentiles(BaseModel):
    p50_scan_seconds: Optional[float]
    p95_scan_seconds: Optional[float]
    p50_score_seconds: Optional[float]
    p95_score_seconds: Optional[float]


class FreshnessSummary(BaseModel):
    total: int
    fresh: int
    stale: int
    unknown: int


class SourceFreshnessEntry(BaseModel):
    registry_source: str
    total: int
    fresh: int
    stale: int
    unknown: int


class ServerFreshnessMetricsResponse(BaseModel):
    total_servers: int
    scan_summary: FreshnessSummary
    score_summary: FreshnessSummary
    percentiles: FreshnessPercentiles
    oldest_scan_seconds: Optional[float]
    oldest_score_seconds: Optional[float]
    by_source: List[SourceFreshnessEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _compute_seconds(ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


def _scan_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    return "stale" if seconds > _SCAN_STALE_SEC else "fresh"


def _score_bucket(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    return "stale" if seconds > _ASSESS_STALE_SEC else "fresh"


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p / 100.0
    lo = int(idx)
    hi = lo + 1
    if hi >= len(sorted_vals):
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/servers/freshness/metrics", response_model=ServerFreshnessMetricsResponse)
def get_server_freshness_metrics(
    db: Session = Depends(get_session),
) -> ServerFreshnessMetricsResponse:
    """Return aggregate freshness metrics across all servers."""
    # Latest score per server
    latest = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_assessed"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    rows = (
        db.query(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.last_scanned,
            latest.c.last_assessed,
        )
        .outerjoin(latest, McpServerRegistry.server_id == latest.c.server_id)
        .all()
    )

    scan_fresh = scan_stale = scan_unknown = 0
    score_fresh = score_stale = score_unknown = 0
    scan_seconds: List[float] = []
    score_seconds: List[float] = []

    # by source
    source_map: dict[str, dict] = {}
    for r in rows:
        src = r.registry_source or "unknown"
        if src not in source_map:
            source_map[src] = {"total": 0, "fresh": 0, "stale": 0, "unknown": 0}

        scan_sec = _compute_seconds(r.last_scanned)
        score_sec = _compute_seconds(r.last_assessed)

        sl = _scan_bucket(scan_sec)
        pl = _score_bucket(score_sec)

        source_map[src]["total"] += 1
        source_map[src][sl] += 1
        source_map[src][pl] += 1

        # scan summary
        if sl == "fresh":
            scan_fresh += 1
        elif sl == "stale":
            scan_stale += 1
        else:
            scan_unknown += 1

        # score summary
        if pl == "fresh":
            score_fresh += 1
        elif pl == "stale":
            score_stale += 1
        else:
            score_unknown += 1

        # for percentiles
        if scan_sec is not None:
            scan_seconds.append(scan_sec)
        if score_sec is not None:
            score_seconds.append(score_sec)

    scan_seconds.sort()
    score_seconds.sort()

    by_source = [
        SourceFreshnessEntry(
            registry_source=src,
            total=v["total"],
            fresh=v["fresh"],
            stale=v["stale"],
            unknown=v["unknown"],
        )
        for src, v in sorted(source_map.items())
    ]

    return ServerFreshnessMetricsResponse(
        total_servers=len(rows),
        scan_summary=FreshnessSummary(
            total=len(rows), fresh=scan_fresh, stale=scan_stale, unknown=scan_unknown
        ),
        score_summary=FreshnessSummary(
            total=len(rows), fresh=score_fresh, stale=score_stale, unknown=score_unknown
        ),
        percentiles=FreshnessPercentiles(
            p50_scan_seconds=_percentile(scan_seconds, 50),
            p95_scan_seconds=_percentile(scan_seconds, 95),
            p50_score_seconds=_percentile(score_seconds, 50),
            p95_score_seconds=_percentile(score_seconds, 95),
        ),
        oldest_scan_seconds=scan_seconds[-1] if scan_seconds else None,
        oldest_score_seconds=score_seconds[-1] if score_seconds else None,
        by_source=by_source,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with TestingSession() as db:
        # srv1: FRESH scan + FRESH score
        db.add(McpServerRegistry(
            server_id="srv1", name="Fresh Server",
            registry_source="vendor",
            last_scanned=now, last_assessed=now,
        ))
        db.add(McpLlmAxisScore(
            id=1, server_id="srv1", axis_name="risk", label="low",
            label_index=0, scored_at=now, model_version="v1",
            adapter_sha256="abc", decision_rule_version="1",
        ))
        # srv2: FRESH scan + FRESH score, different source
        db.add(McpServerRegistry(
            server_id="srv2", name="Also Fresh",
            registry_source="community",
            last_scanned=now, last_assessed=now,
        ))
        db.add(McpLlmAxisScore(
            id=2, server_id="srv2", axis_name="risk", label="medium",
            label_index=1, scored_at=now, model_version="v1",
            adapter_sha256="abc", decision_rule_version="1",
        ))
        # srv3: STALE scan + STALE score
        db.add(McpServerRegistry(
            server_id="srv3", name="Stale Server",
            registry_source="vendor",
            last_scanned=now - timedelta(days=10),
            last_assessed=now - timedelta(days=10),
        ))
        db.add(McpLlmAxisScore(
            id=3, server_id="srv3", axis_name="risk", label="high",
            label_index=3, scored_at=now - timedelta(days=10),
            model_version="v1", adapter_sha256="abc",
            decision_rule_version="1",
        ))
        # srv4: NEVER scan / score
        db.add(McpServerRegistry(
            server_id="srv4", name="Unknown Server",
            registry_source="community",
            last_scanned=None, last_assessed=None,
        ))
        db.commit()

    client = TestClient(app)

    r = client.get("/api/servers/freshness/metrics")
    assert r.status_code == 200, f"got {r.status_code}"
    d = r.json()

    assert d["total_servers"] == 4, f"total={d['total_servers']}"

    # scan: srv1=fresh, srv2=fresh, srv3=stale, srv4=unknown
    assert d["scan_summary"]["fresh"] == 2, f"scan_fresh={d['scan_summary']['fresh']}"
    assert d["scan_summary"]["stale"] == 1, f"scan_stale={d['scan_summary']['stale']}"
    assert d["scan_summary"]["unknown"] == 1, f"scan_unknown={d['scan_summary']['unknown']}"

    # score: same as scan in this dataset
    assert d["score_summary"]["fresh"] == 2
    assert d["score_summary"]["stale"] == 1
    assert d["score_summary"]["unknown"] == 1

    # percentiles should be computed
    assert d["percentiles"]["p50_scan_seconds"] is not None
    assert d["percentiles"]["p95_scan_seconds"] is not None
    # srv3 is ~864000 seconds (10 days); p95 should be near that
    assert d["percentiles"]["p95_scan_seconds"] is not None
    assert d["percentiles"]["p95_scan_seconds"] > 0

    # oldest should be srv3 (10 days)
    assert d["oldest_scan_seconds"] is not None
    assert d["oldest_scan_seconds"] > 800000  # ~10 days in seconds

    # by_source: vendor (srv1,srv3), community (srv2,srv4)
    assert len(d["by_source"]) == 2, f"sources={len(d['by_source'])}"
    by_src = {e["registry_source"]: e for e in d["by_source"]}
    assert by_src["vendor"]["total"] == 2
    assert by_src["vendor"]["stale"] == 1
    assert by_src["community"]["total"] == 2
    assert by_src["community"]["unknown"] == 1

    # unknown source
    db.add(McpServerRegistry(
        server_id="srv5", name="No Source",
        registry_source=None,
        last_scanned=now, last_assessed=now,
    ))
    db.add(McpLlmAxisScore(
        id=4, server_id="srv5", axis_name="risk", label="low",
        label_index=0, scored_at=now, model_version="v1",
        adapter_sha256="abc", decision_rule_version="1",
    ))
    db.commit()

    r2 = client.get("/api/servers/freshness/metrics")
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["total_servers"] == 5
    assert any(e["registry_source"] == "unknown" for e in d2["by_source"])

    print("PASS")
