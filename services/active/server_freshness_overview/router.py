# deps: fastapi, pydantic, requests
"""FastAPI router for server-freshness overview.

Returns aggregate freshness summary across all servers:
- counts by freshness bucket (FRESH / STALE / NEVER)
- counts by scan status and score status
- optional top-N stale server list

Queries the app DB (McpServerRegistry, McpLlmAxisScore).
Public access, no auth required.
"""
from __future__ import annotations

import os
import sys as _sys

# Must be first -- repo root must be on sys.path before app.* imports at any scope
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

router = APIRouter(prefix="/api", tags=["server_freshness_overview"])

# Thresholds in seconds
_SCAN_STALE_SEC = 7 * 24 * 3600    # 7 days
_ASSESS_STALE_SEC = 7 * 24 * 3600  # 7 days


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class StatusCounts(BaseModel):
    fresh: int
    stale: int
    never: int


class FreshnessBuckets(BaseModel):
    fresh: int
    stale: int
    unknown: int


class TopStaleServer(BaseModel):
    server_id: str
    name: str
    days_since_scan: Optional[int]
    days_since_score: Optional[int]


class ServerFreshnessOverviewResponse(BaseModel):
    total_servers: int
    scan_status: StatusCounts
    score_status: StatusCounts
    freshness_buckets: FreshnessBuckets
    top_stale_servers: List[TopStaleServer]


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


def _scan_label(seconds: Optional[float]) -> str:
    if seconds is None:
        return "never"
    return "stale" if seconds > _SCAN_STALE_SEC else "fresh"


def _score_label(seconds: Optional[float]) -> str:
    if seconds is None:
        return "never"
    return "stale" if seconds > _ASSESS_STALE_SEC else "fresh"


def _freshness_bucket(scan_label: str, score_label: str) -> str:
    if scan_label == "never" and score_label == "never":
        return "unknown"
    if scan_label == "stale" or score_label == "stale":
        return "stale"
    return "fresh"


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #


@router.get("/servers/freshness/overview", response_model=ServerFreshnessOverviewResponse)
def get_server_freshness_overview(
    limit: int = Query(default=10, ge=0, le=100),
    db: Session = Depends(get_session),
) -> ServerFreshnessOverviewResponse:
    """Return aggregate freshness overview across all servers."""
    now = datetime.now(timezone.utc)

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
            McpServerRegistry.last_scanned,
            latest.c.last_assessed,
        )
        .outerjoin(latest, McpServerRegistry.server_id == latest.c.server_id)
        .all()
    )

    scan_fresh = scan_stale = scan_never = 0
    score_fresh = score_stale = score_never = 0
    bucket_fresh = bucket_stale = bucket_unknown = 0
    stale_entries: List[dict] = []

    for r in rows:
        scan_sec = _compute_seconds(r.last_scanned)
        score_sec = _compute_seconds(r.last_assessed)

        sl = _scan_label(scan_sec)
        pl = _score_label(score_sec)
        fb = _freshness_bucket(sl, pl)

        # Scan counts
        if sl == "fresh":
            scan_fresh += 1
        elif sl == "stale":
            scan_stale += 1
        else:
            scan_never += 1

        # Score counts
        if pl == "fresh":
            score_fresh += 1
        elif pl == "stale":
            score_stale += 1
        else:
            score_never += 1

        # Bucket counts
        if fb == "fresh":
            bucket_fresh += 1
        elif fb == "stale":
            bucket_stale += 1
        else:
            bucket_unknown += 1

        # Track stale entries for top-N
        if sl == "stale" or pl == "stale":
            days_scan = (
                int((now - r.last_scanned).total_seconds()) // 86400
                if r.last_scanned
                else None
            )
            days_score = (
                int((now - r.last_assessed).total_seconds()) // 86400
                if r.last_assessed
                else None
            )
            stale_entries.append({
                "server_id": r.server_id,
                "name": r.name or "",
                "days_since_scan": days_scan,
                "days_since_score": days_score,
            })

    # Sort by worst freshness (most stale days)
    stale_entries.sort(
        key=lambda x: max(
            x["days_since_scan"] or 0, x["days_since_score"] or 0
        ),
        reverse=True,
    )
    top_stale: List[TopStaleServer] = [
        TopStaleServer(**e) for e in stale_entries[:limit]
    ]

    return ServerFreshnessOverviewResponse(
        total_servers=len(rows),
        scan_status=StatusCounts(fresh=scan_fresh, stale=scan_stale, never=scan_never),
        score_status=StatusCounts(fresh=score_fresh, stale=score_stale, never=score_never),
        freshness_buckets=FreshnessBuckets(
            fresh=bucket_fresh, stale=bucket_stale, unknown=bucket_unknown
        ),
        top_stale_servers=top_stale,
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
        # srv1: FRESH scan + FRESH score = FRESH bucket
        db.add(McpServerRegistry(
            server_id="srv1", name="Fresh Server",
            last_scanned=now, last_assessed=now,
        ))
        db.add(McpLlmAxisScore(
            id=1, server_id="srv1", axis_name="risk", label="low",
            label_index=0, scored_at=now, model_version="v1",
            adapter_sha256="abc", decision_rule_version="1",
        ))
        # srv2: STALE scan (8d) + FRESH score = STALE bucket
        db.add(McpServerRegistry(
            server_id="srv2", name="Stale Scan",
            last_scanned=now - timedelta(days=8), last_assessed=now,
        ))
        db.add(McpLlmAxisScore(
            id=2, server_id="srv2", axis_name="risk", label="high",
            label_index=3, scored_at=now, model_version="v1",
            adapter_sha256="abc", decision_rule_version="1",
        ))
        # srv3: NEVER scan + NEVER score = UNKNOWN bucket
        db.add(McpServerRegistry(
            server_id="srv3", name="Never Server",
            last_scanned=None, last_assessed=None,
        ))
        # srv4: STALE scan + STALE score = STALE bucket (top of list)
        db.add(McpServerRegistry(
            server_id="srv4", name="Worst Server",
            last_scanned=now - timedelta(days=30),
            last_assessed=now - timedelta(days=30),
        ))
        db.add(McpLlmAxisScore(
            id=3, server_id="srv4", axis_name="risk", label="critical",
            label_index=4, scored_at=now - timedelta(days=30),
            model_version="v1", adapter_sha256="abc",
            decision_rule_version="1",
        ))
        db.commit()

    client = TestClient(app)

    r = client.get("/api/servers/freshness/overview")
    assert r.status_code == 200, f"got {r.status_code}"
    d = r.json()

    assert d["total_servers"] == 4, f"total={d['total_servers']}"

    # scan: srv1=fresh, srv2=stale, srv3=never, srv4=stale
    assert d["scan_status"]["fresh"] == 1, f"scan_fresh={d['scan_status']['fresh']}"
    assert d["scan_status"]["stale"] == 2, f"scan_stale={d['scan_status']['stale']}"
    assert d["scan_status"]["never"] == 1, f"scan_never={d['scan_status']['never']}"

    # score: srv1=fresh, srv2=fresh, srv3=never, srv4=stale
    assert d["score_status"]["fresh"] == 2, f"score_fresh={d['score_status']['fresh']}"
    assert d["score_status"]["stale"] == 1, f"score_stale={d['score_status']['stale']}"
    assert d["score_status"]["never"] == 1, f"score_never={d['score_status']['never']}"

    # buckets: srv1=fresh, srv2=stale, srv3=unknown, srv4=stale
    assert d["freshness_buckets"]["fresh"] == 1
    assert d["freshness_buckets"]["stale"] == 2
    assert d["freshness_buckets"]["unknown"] == 1

    # top stale: srv4 (30d) first, then srv2 (8d)
    assert len(d["top_stale_servers"]) == 2, f"top={len(d['top_stale_servers'])}"
    assert d["top_stale_servers"][0]["server_id"] == "srv4"
    assert d["top_stale_servers"][1]["server_id"] == "srv2"

    # limit param
    r2 = client.get("/api/servers/freshness/overview?limit=1")
    assert r2.status_code == 200
    assert len(r2.json()["top_stale_servers"]) == 1

    print("PASS")
