# deps: fastapi, pydantic, sqlalchemy
"""Server Verdict Timeline API.

Provides per-server verdict-timeline endpoints — the chronological history of
axis-label assignments and derived risk tiers for a given MCP server.

Public endpoint (auth=public) — no authentication required.
Data source: mcp_server_registry + mcp_llm_axis_scores via get_session.

GET /api/servers/{server_id}/verdict-timeline
GET /api/servers/{server_id}/verdict-timeline/latest
"""
from __future__ import annotations

import os
import sys

# Hardcode repo root so __main__ self-test works regardless of CWD
_REPO_ROOT = "/home/workspace/zo_sentinel"
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_verdict_timeline_api"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SeriesEntry(BaseModel):
    """One scoring-event snapshot: scored_at + derived risk tier + per-axis labels."""
    scored_at: datetime
    risk_tier: str
    axes: Dict[str, Optional[str]]

    model_config = ConfigDict(from_attributes=True)


class ServerVerdictTimelineResponse(BaseModel):
    """Full chronological series for a server."""
    server_id: str
    server_name: Optional[str]
    series: List[SeriesEntry]

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _risk_tier_from_axes(
    axes: Dict[str, Optional[str]], url: Optional[str], name: Optional[str]
) -> str:
    """Derive published risk tier via trust_gate override."""
    try:
        from trust_gating_override import trust_gate
        result = trust_gate(url, name, axes)
        return result.get("published_overall_risk", "UNKNOWN")
    except Exception:
        return axes.get("overall_risk") or "UNKNOWN"


def _build_timeline(
    db: Session, server_id: str
) -> tuple[Optional[McpServerRegistry], List[dict]]:
    """Query axis scores, group by scored_at, derive risk tier per event."""
    server = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()

    rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.asc())
        .all()
    )

    grouped: Dict[datetime, List[McpLlmAxisScore]] = defaultdict(list)
    for row in rows:
        grouped[row.scored_at].append(row)

    series: List[dict] = []
    for scored_at in sorted(grouped.keys()):
        group = grouped[scored_at]
        ax = {r.axis_name: r.label for r in group if r.label}
        risk_tier = _risk_tier_from_axes(
            ax, server.url if server else None, server.name if server else None
        )
        series.append({
            "scored_at": scored_at,
            "risk_tier": risk_tier,
            "axes": ax,
        })

    return server, series


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/servers/{server_id}/verdict-timeline",
    response_model=ServerVerdictTimelineResponse,
    summary="Get full verdict timeline for a server",
    responses={404: {"description": "Server not found"}},
)
def get_verdict_timeline(
    server_id: str,
    days: int = Query(default=90, ge=1, le=730, description="Days of history to return"),
    db: Session = Depends(get_session),
) -> ServerVerdictTimelineResponse:
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    _, series = _build_timeline(db, server_id)

    cutoff = datetime.utcnow().replace(microsecond=0) - timedelta(days=days)
    series = [e for e in series if e["scored_at"] >= cutoff]

    return ServerVerdictTimelineResponse(
        server_id=server_id,
        server_name=srv.name,
        series=[SeriesEntry(**e) for e in series],
    )


@router.get(
    "/servers/{server_id}/verdict-timeline/latest",
    response_model=SeriesEntry,
    summary="Get the most recent verdict snapshot for a server",
    responses={404: {"description": "Server not found or has no scores"}},
)
def get_latest_verdict(
    server_id: str,
    db: Session = Depends(get_session),
) -> SeriesEntry:
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    latest_row = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )
    if not latest_row:
        raise HTTPException(status_code=404, detail="No scores found for this server")

    all_axes = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.scored_at == latest_row.scored_at,
        )
        .all()
    )
    ax = {r.axis_name: r.label for r in all_axes if r.label}
    risk_tier = _risk_tier_from_axes(ax, srv.url, srv.name)

    return SeriesEntry(
        scored_at=latest_row.scored_at,
        risk_tier=risk_tier,
        axes=ax,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys as _sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override

    now = datetime.now(tz=datetime.utcnow().tzinfo) if datetime.utcnow().tzinfo else datetime.now()
    # Use naive utcnow for SQLite compatibility in test (SQLite DATETIME has no tz)
    _now = datetime.utcnow()
    t1 = _now - timedelta(days=3)
    t2 = _now - timedelta(days=1)
    t3 = _now

    with TestSession() as db:
        db.add(McpServerRegistry(
            server_id="srv-tl-1",
            name="Timeline Test Server",
            url="https://github.com/test-org/test-server",
            risk_tier="HIGH",
        ))
        db.add(McpServerRegistry(
            server_id="srv-tl-2",
            name="Empty Server",
            risk_tier="LOW",
        ))
        db.add(McpLlmAxisScore(
            id=1,
            server_id="srv-tl-1", axis_name="overall_risk",
            label="LOW", label_index=0, p_top=0.15,
            model_version="v1", scored_at=t1,
        ))
        db.add(McpLlmAxisScore(
            id=2,
            server_id="srv-tl-1", axis_name="auth_strength",
            label="STRONG", label_index=3, p_top=0.80,
            model_version="v1", scored_at=t1,
        ))
        db.add(McpLlmAxisScore(
            id=3,
            server_id="srv-tl-1", axis_name="overall_risk",
            label="MEDIUM", label_index=1, p_top=0.45,
            model_version="v2", scored_at=t2,
        ))
        db.add(McpLlmAxisScore(
            id=4,
            server_id="srv-tl-1", axis_name="auth_strength",
            label="STRONG", label_index=3, p_top=0.85,
            model_version="v2", scored_at=t2,
        ))
        db.add(McpLlmAxisScore(
            id=5,
            server_id="srv-tl-1", axis_name="overall_risk",
            label="HIGH", label_index=2, p_top=0.70,
            model_version="v3", scored_at=t3,
        ))
        db.add(McpLlmAxisScore(
            id=6,
            server_id="srv-tl-1", axis_name="auth_strength",
            label="WEAK", label_index=0, p_top=0.20,
            model_version="v3", scored_at=t3,
        ))
        db.commit()

    client = TestClient(test_app)

    # Test 1: full timeline — happy path
    r = client.get("/api/servers/srv-tl-1/verdict-timeline")
    if r.status_code != 200:
        print(f"FAIL: timeline returned {r.status_code}: {r.text}")
        _sys.exit(1)
    d = r.json()
    if d["server_id"] != "srv-tl-1":
        print(f"FAIL: wrong server_id: {d['server_id']}")
        _sys.exit(1)
    if len(d["series"]) != 3:
        print(f"FAIL: expected 3 series entries, got {len(d['series'])}")
        _sys.exit(1)
    if d["series"][0]["risk_tier"] != "LOW":
        print(f"FAIL: first entry wrong risk_tier: {d['series'][0]['risk_tier']}")
        _sys.exit(1)
    if d["series"][-1]["risk_tier"] != "HIGH":
        print(f"FAIL: last entry wrong risk_tier: {d['series'][-1]['risk_tier']}")
        _sys.exit(1)
    if "overall_risk" not in d["series"][0]["axes"]:
        print(f"FAIL: axes missing overall_risk: {d['series'][0]['axes']}")
        _sys.exit(1)

    # Test 2: latest snapshot
    r2 = client.get("/api/servers/srv-tl-1/verdict-timeline/latest")
    if r2.status_code != 200:
        print(f"FAIL: latest returned {r2.status_code}: {r2.text}")
        _sys.exit(1)
    d2 = r2.json()
    if d2["risk_tier"] != "HIGH":
        print(f"FAIL: latest wrong risk_tier: {d2['risk_tier']}")
        _sys.exit(1)
    if "overall_risk" not in d2["axes"]:
        print(f"FAIL: latest axes missing overall_risk: {d2['axes']}")
        _sys.exit(1)

    # Test 3: 404 for unknown server
    r3 = client.get("/api/servers/nonexistent-srv/verdict-timeline")
    if r3.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {r3.status_code}")
        _sys.exit(1)

    # Test 4: latest 404 for server with no scores
    r4 = client.get("/api/servers/srv-tl-2/verdict-timeline/latest")
    if r4.status_code != 404:
        print(f"FAIL: expected 404 for server with no scores, got {r4.status_code}")
        _sys.exit(1)

    # Test 5: days filter
    r5 = client.get("/api/servers/srv-tl-1/verdict-timeline?days=2")
    if r5.status_code != 200:
        print(f"FAIL: days filter failed: {r5.status_code}: {r5.text}")
        _sys.exit(1)
    d5 = r5.json()
    if len(d5["series"]) > 2:
        print(f"FAIL: days filter did not reduce series (got {len(d5['series'])})")
        _sys.exit(1)

    print("PASS")
    _sys.exit(0)
