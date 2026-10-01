# deps: fastapi, sqlalchemy, pydantic
"""Server Verdict History Service.

Returns the risk-tier transition history for an MCP server:
  - chronological PerspectiveEvent rows (tier enter/leave/change)
  - score snapshots at the same time points (from mcp_llm_axis_scores)

Public endpoint — no authentication required.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import asc, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, PerspectiveEvent

router = APIRouter(prefix="/api", tags=["server_verdict_history"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class VerdictHistoryEntry(BaseModel):
    """One point-in-time record: the tier verdict + all 7 axis scores at scored_at."""
    event_id: int
    change_type: str
    old_tier: Optional[str]
    new_tier: Optional[str]
    scored_at: datetime
    axes: list["AxisSnapshotEntry"]


class AxisSnapshotEntry(BaseModel):
    axis_name: str
    label: Optional[str]
    p_top: Optional[float]
    p_critical: Optional[float]
    p_danger: Optional[float]
    escalated: Optional[bool]


class ServerVerdictHistoryResponse(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    entries: list[VerdictHistoryEntry]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_server(db: Session, server_id: str) -> McpServerRegistry:
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return srv


def _latest_model_version(db: Session, server_id: str) -> Optional[str]:
    """Return the most recent model_version for this server, or None."""
    row = db.query(func.max(McpLlmAxisScore.model_version)).filter(
        McpLlmAxisScore.server_id == server_id
    ).scalar()
    return row  # type: ignore[return-value]


def _scores_at_version(
    db: Session, server_id: str, model_version: str, scored_at: datetime
) -> list[AxisSnapshotEntry]:
    """Return axis scores closest to scored_at for a given model_version."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
            McpLlmAxisScore.scored_at <= scored_at,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    # Deduplicate by axis_name (keep first/most-recent per axis)
    seen: set[str] = set()
    entries: list[AxisSnapshotEntry] = []
    for r in rows:
        if r.axis_name not in seen:
            seen.add(r.axis_name)
            entries.append(AxisSnapshotEntry(
                axis_name=r.axis_name,
                label=r.label,
                p_top=r.p_top,
                p_critical=r.p_critical,
                p_danger=r.p_danger,
                escalated=r.escalated,
            ))
    return entries


def _build_entries(
    db: Session, server_id: str, events: list[PerspectiveEvent]
) -> list[VerdictHistoryEntry]:
    """
    For each perspective event, pick up to 1 score snapshot nearest to its
    created_at timestamp using the current model_version.  Falls back to an
    empty axis list if no scores align.
    """
    model_version = _latest_model_version(db, server_id)
    if not model_version:
        # No scores at all — return events with empty axes
        return [
            VerdictHistoryEntry(
                event_id=e.id,
                change_type=e.change_type,
                old_tier=e.old_tier,
                new_tier=e.new_tier,
                scored_at=e.created_at,
                axes=[],
            )
            for e in events
        ]

    entries: list[VerdictHistoryEntry] = []
    for ev in events:
        axes = _scores_at_version(db, server_id, model_version, ev.created_at)
        entries.append(VerdictHistoryEntry(
            event_id=ev.id,
            change_type=ev.change_type,
            old_tier=ev.old_tier,
            new_tier=ev.new_tier,
            scored_at=ev.created_at,
            axes=axes,
        ))
    return entries


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/server-verdict-history/{server_id}",
    response_model=ServerVerdictHistoryResponse,
    summary="Get full verdict history for a server",
    responses={404: {"description": "Server not found"}},
)
def get_server_verdict_history(
    server_id: str,
    limit: int = Query(default=50, ge=1, le=500, description="Max events to return"),
    db: Session = Depends(get_session),
) -> ServerVerdictHistoryResponse:
    """
    Returns the chronological verdict / risk-tier transition history for a server.
    Each entry pairs a PerspectiveEvent (tier change) with the axis-score snapshot
    nearest in time.
    """
    srv = _fetch_server(db, server_id)

    events = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(asc(PerspectiveEvent.created_at))
        .limit(limit)
        .all()
    )

    entries = _build_entries(db, server_id, events)

    return ServerVerdictHistoryResponse(
        server_id=server_id,
        name=srv.name,
        risk_tier=srv.risk_tier,
        entries=entries,
    )


@router.get(
    "/server-verdict-history/{server_id}/latest",
    response_model=VerdictHistoryEntry,
    summary="Get the most recent verdict entry for a server",
    responses={404: {"description": "Server not found"}},
)
def get_latest_verdict(
    server_id: str,
    db: Session = Depends(get_session),
) -> VerdictHistoryEntry:
    """
    Returns the latest PerspectiveEvent for a server plus its nearest axis-score
    snapshot.
    """
    _fetch_server(db, server_id)  # raise 404

    ev = (
        db.query(PerspectiveEvent)
        .filter(PerspectiveEvent.server_id == server_id)
        .order_by(asc(PerspectiveEvent.created_at))
        .first()
    )

    if not ev:
        raise HTTPException(
            status_code=404,
            detail=f"No verdict history found for server {server_id}",
        )

    entries = _build_entries(db, server_id, [ev])
    return entries[0]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False
    )

    from app.models import Base
    Base.metadata.create_all(test_engine)

    def _override_get_session():
        sess = TestSessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()
    t1 = now
    t2 = now
    t3 = now

    with TestSessionLocal() as sess:
        sess.add(McpServerRegistry(
            server_id="srv-history-1", name="History Test Server", risk_tier="HIGH",
        ))
        sess.add(McpServerRegistry(
            server_id="srv-history-2", name="Empty History Server", risk_tier="LOW",
        ))
        # 3 events for srv-history-1
        sess.add(PerspectiveEvent(
            id=10, server_id="srv-history-1", change_type="initial",
            old_tier=None, new_tier="LOW", created_at=t1,
        ))
        sess.add(PerspectiveEvent(
            id=11, server_id="srv-history-1", change_type="tier_upgrade",
            old_tier="LOW", new_tier="MEDIUM", created_at=t2,
        ))
        sess.add(PerspectiveEvent(
            id=12, server_id="srv-history-1", change_type="tier_upgrade",
            old_tier="MEDIUM", new_tier="HIGH", created_at=t3,
        ))
        # axis scores
        sess.add(McpLlmAxisScore(
            server_id="srv-history-1", axis_name="overall_risk",
            label="LOW", p_top=0.15, model_version="v1", scored_at=t1,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-history-1", axis_name="overall_risk",
            label="MEDIUM", p_top=0.45, model_version="v1", scored_at=t2,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-history-1", axis_name="overall_risk",
            label="HIGH", p_top=0.65, model_version="v1", scored_at=t3,
        ))
        sess.commit()

    client = TestClient(test_app)

    # --- happy path: full history ---
    resp = client.get("/api/server-verdict-history/srv-history-1")
    if resp.status_code != 200:
        print(f"FAIL: history endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv-history-1":
        print(f"FAIL: wrong server_id: {data['server_id']}")
        sys.exit(1)
    if len(data["entries"]) != 3:
        print(f"FAIL: expected 3 entries, got {len(data['entries'])}")
        sys.exit(1)
    # check tier transitions
    if data["entries"][0]["new_tier"] != "LOW":
        print(f"FAIL: first entry wrong new_tier: {data['entries'][0]['new_tier']}")
        sys.exit(1)
    if data["entries"][1]["old_tier"] != "LOW":
        print(f"FAIL: second entry wrong old_tier: {data['entries'][1]['old_tier']}")
        sys.exit(1)
    if data["entries"][1]["new_tier"] != "MEDIUM":
        print(f"FAIL: second entry wrong new_tier: {data['entries'][1]['new_tier']}")
        sys.exit(1)

    # --- happy path: latest ---
    resp2 = client.get("/api/server-verdict-history/srv-history-1/latest")
    if resp2.status_code != 200:
        print(f"FAIL: latest endpoint returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    latest = resp2.json()
    if latest["change_type"] != "tier_upgrade":
        print(f"FAIL: latest change_type wrong: {latest['change_type']}")
        sys.exit(1)
    if latest["new_tier"] != "HIGH":
        print(f"FAIL: latest new_tier wrong: {latest['new_tier']}")
        sys.exit(1)

    # --- 404 for unknown server ---
    resp3 = client.get("/api/server-verdict-history/nonexistent-server")
    if resp3.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp3.status_code}")
        sys.exit(1)

    # --- empty history server (200, empty entries) ---
    resp4 = client.get("/api/server-verdict-history/srv-history-2")
    if resp4.status_code != 200:
        print(f"FAIL: empty history returned {resp4.status_code}")
        sys.exit(1)
    if len(resp4.json()["entries"]) != 0:
        print(f"FAIL: expected 0 entries for srv-history-2")
        sys.exit(1)

    # --- latest 404 for server with no events ---
    resp5 = client.get("/api/server-verdict-history/srv-history-2/latest")
    if resp5.status_code != 404:
        print(f"FAIL: expected 404 for latest on empty server, got {resp5.status_code}")
        sys.exit(1)

    print("PASS")
