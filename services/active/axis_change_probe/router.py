# deps: fastapi, sqlalchemy, pydantic
"""Axis Change Probe Service.

Detects changes in LLM axis scores for MCP servers by comparing the latest
score snapshot against historical records within a configurable lookback window.
Public endpoint — no authentication required.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_change_probe"])


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class AxisChangeEntry(BaseModel):
    axis_name: str
    current_label: Optional[str]
    previous_label: Optional[str]
    current_p_top: Optional[float]
    previous_p_top: Optional[float]
    changed: bool
    direction: Optional[str]  # "escalated" | "de-escalated" | "new" | "dropped" | None

    model_config = ConfigDict(from_attributes=True)


class ServerAxisChangesResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    axes: List[AxisChangeEntry]
    total_axes: int
    changed_axes: int
    lookback_days: int
    model_version: Optional[str]


class AxisChangeSummary(BaseModel):
    total_servers: int
    servers_with_changes: int
    total_axis_changes: int
    lookback_days: int
    servers: List[ServerAxisChangesResponse]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TIER_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _tier_score(label: Optional[str]) -> int:
    return _TIER_ORDER.get(label, 99) if label else 99


def _direction(current_label: Optional[str], previous_label: Optional[str]) -> Optional[str]:
    if current_label is None and previous_label is not None:
        return "dropped"
    if current_label is not None and previous_label is None:
        return "new"
    if current_label is None or previous_label is None:
        return None
    curr = _tier_score(current_label)
    prev = _tier_score(previous_label)
    if curr < prev:
        return "escalated"
    if curr > prev:
        return "de-escalated"
    return None


def _fetch_latest_scores(
    db: Session, server_id: str, model_version: str
) -> Dict[str, McpLlmAxisScore]:
    """Return a dict axis_name -> latest score row."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: Dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r
    return seen


def _fetch_previous_scores(
    db: Session, server_id: str, model_version: str, cutoff: datetime
) -> Dict[str, McpLlmAxisScore]:
    """Return a dict axis_name -> most-recent score row before the cutoff."""
    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.model_version == model_version,
            McpLlmAxisScore.scored_at < cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    seen: Dict[str, McpLlmAxisScore] = {}
    for r in rows:
        if r.axis_name not in seen:
            seen[r.axis_name] = r
    return seen


def _build_axis_changes(
    db: Session, server_id: str, lookback_days: int
) -> tuple[List[AxisChangeEntry], Optional[str]]:
    """Return axis change entries and the model_version used."""
    now = datetime.utcnow()
    cutoff = now - timedelta(days=lookback_days)

    # Get the latest model_version for this server
    version_row = (
        db.query(McpLlmAxisScore.model_version)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(desc(McpLlmAxisScore.scored_at))
        .first()
    )
    if version_row is None:
        return [], None
    model_version = version_row[0]

    latest = _fetch_latest_scores(db, server_id, model_version)
    previous = _fetch_previous_scores(db, server_id, model_version, cutoff)

    all_axes = set(latest.keys()) | set(previous.keys())
    entries: List[AxisChangeEntry] = []
    for axis in sorted(all_axes):
        cur = latest.get(axis)
        prev = previous.get(axis)
        cur_label = cur.label if cur else None
        prev_label = prev.label if prev else None
        entries.append(AxisChangeEntry(
            axis_name=axis,
            current_label=cur_label,
            previous_label=prev_label,
            current_p_top=cur.p_top if cur else None,
            previous_p_top=prev.p_top if prev else None,
            changed=_direction(cur_label, prev_label) is not None,
            direction=_direction(cur_label, prev_label),
        ))
    return entries, model_version


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/axis-changes",
    response_model=AxisChangeSummary,
    summary="Get axis change summary for all servers",
)
def get_all_axis_changes(
    lookback_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> AxisChangeSummary:
    """
    Return axis score changes for all servers within the lookback window.
    """
    # Get all servers that have any axis scores
    server_ids = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .all()
    )
    server_ids = [r[0] for r in server_ids]

    servers_out: List[ServerAxisChangesResponse] = []
    total_changes = 0
    servers_with_changes = 0

    for sid in server_ids:
        entries, _ = _build_axis_changes(db, sid, lookback_days)
        changed_axes = sum(1 for e in entries if e.changed)
        if changed_axes:
            servers_with_changes += 1
        total_changes += changed_axes

        # Fetch server name
        srv = db.query(McpServerRegistry.name).filter(
            McpServerRegistry.server_id == sid
        ).first()
        name = srv[0] if srv else None

        servers_out.append(ServerAxisChangesResponse(
            server_id=sid,
            server_name=name,
            axes=entries,
            total_axes=len(entries),
            changed_axes=changed_axes,
            lookback_days=lookback_days,
            model_version=None,
        ))

    return AxisChangeSummary(
        total_servers=len(servers_out),
        servers_with_changes=servers_with_changes,
        total_axis_changes=total_changes,
        lookback_days=lookback_days,
        servers=servers_out,
    )


@router.get(
    "/axis-changes/{server_id}",
    response_model=ServerAxisChangesResponse,
    summary="Get axis change details for a specific server",
    responses={404: {"description": "Server not found or has no axis scores"}},
)
def get_server_axis_changes(
    server_id: str,
    lookback_days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ServerAxisChangesResponse:
    """
    Return detailed axis score changes for a specific server.
    Compares the latest score snapshot against the most-recent record
    before the lookback cutoff.
    """
    # Verify server exists in registry
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if srv is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    entries, model_version = _build_axis_changes(db, server_id, lookback_days)
    if model_version is None:
        raise HTTPException(
            status_code=404,
            detail=f"No axis scores found for server {server_id}",
        )

    return ServerAxisChangesResponse(
        server_id=server_id,
        server_name=srv.name,
        axes=entries,
        total_axes=len(entries),
        changed_axes=sum(1 for e in entries if e.changed),
        lookback_days=lookback_days,
        model_version=model_version,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    from app.models import Base
    Base.metadata.create_all(engine)

    def _override_get_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    now = datetime.utcnow()
    t_cutoff = now - timedelta(days=30)
    t_old = now - timedelta(days=60)
    t_recent = now - timedelta(days=5)

    with SessionLocal() as sess:
        sess.add(McpServerRegistry(server_id="srv-probe-1", name="Probe Server One", risk_tier="HIGH"))
        sess.add(McpServerRegistry(server_id="srv-probe-2", name="Probe Server Two", risk_tier="LOW"))
        sess.add(McpServerRegistry(server_id="srv-probe-3", name="Probe Server Three", risk_tier="MEDIUM"))

        # srv-probe-1: axis changed from LOW -> HIGH within lookback
        sess.add(McpLlmAxisScore(
            server_id="srv-probe-1", axis_name="overall_risk",
            label="LOW", p_top=0.2, model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-probe-1", axis_name="overall_risk",
            label="HIGH", p_top=0.75, model_version="v1", scored_at=t_recent,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-probe-1", axis_name="security",
            label="MEDIUM", p_top=0.45, model_version="v1", scored_at=t_recent,
        ))

        # srv-probe-2: no changes
        sess.add(McpLlmAxisScore(
            server_id="srv-probe-2", axis_name="overall_risk",
            label="LOW", p_top=0.1, model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            server_id="srv-probe-2", axis_name="overall_risk",
            label="LOW", p_top=0.15, model_version="v1", scored_at=t_recent,
        ))

        # srv-probe-3: new axis (wasn't scored before cutoff)
        sess.add(McpLlmAxisScore(
            server_id="srv-probe-3", axis_name="overall_risk",
            label="MEDIUM", p_top=0.55, model_version="v1", scored_at=t_recent,
        ))

        sess.commit()

    client = TestClient(app)

    # --- happy path: per-server endpoint ---
    resp = client.get("/api/axis-changes/srv-probe-1", params={"lookback_days": 30})
    if resp.status_code != 200:
        print(f"FAIL: server endpoint returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["server_id"] != "srv-probe-1":
        print(f"FAIL: wrong server_id: {data['server_id']}")
        sys.exit(1)
    if data["server_name"] != "Probe Server One":
        print(f"FAIL: wrong server_name: {data['server_name']}")
        sys.exit(1)
    if data["total_axes"] < 2:
        print(f"FAIL: expected at least 2 axes for srv-probe-1, got {data['total_axes']}")
        sys.exit(1)
    if data["changed_axes"] < 1:
        print(f"FAIL: expected at least 1 changed axis for srv-probe-1, got {data['changed_axes']}")
        sys.exit(1)

    # --- escalated axis detected ---
    overall_entry = next((e for e in data["axes"] if e["axis_name"] == "overall_risk"), None)
    if overall_entry is None:
        print("FAIL: no overall_risk axis entry")
        sys.exit(1)
    if overall_entry["direction"] != "escalated":
        print(f"FAIL: expected escalated, got {overall_entry['direction']}")
        sys.exit(1)
    if overall_entry["changed"] is not True:
        print(f"FAIL: expected changed=True for overall_risk")
        sys.exit(1)

    # --- new axis detected ---
    security_entry = next((e for e in data["axes"] if e["axis_name"] == "security"), None)
    if security_entry is None:
        print("FAIL: no security axis entry")
        sys.exit(1)
    if security_entry["direction"] != "new":
        print(f"FAIL: expected new for security, got {security_entry['direction']}")
        sys.exit(1)

    # --- no changes server ---
    resp2 = client.get("/api/axis-changes/srv-probe-2", params={"lookback_days": 30})
    if resp2.status_code != 200:
        print(f"FAIL: srv-probe-2 returned {resp2.status_code}: {resp2.text}")
        sys.exit(1)
    data2 = resp2.json()
    if data2["changed_axes"] != 0:
        print(f"FAIL: expected 0 changed axes for srv-probe-2, got {data2['changed_axes']}")
        sys.exit(1)

    # --- 404 for unknown server ---
    resp3 = client.get("/api/axis-changes/nonexistent-server")
    if resp3.status_code != 404:
        print(f"FAIL: expected 404 for unknown server, got {resp3.status_code}")
        sys.exit(1)

    # --- summary endpoint ---
    resp4 = client.get("/api/axis-changes", params={"lookback_days": 30})
    if resp4.status_code != 200:
        print(f"FAIL: summary endpoint returned {resp4.status_code}: {resp4.text}")
        sys.exit(1)
    summary = resp4.json()
    if summary["lookback_days"] != 30:
        print(f"FAIL: wrong lookback_days in summary: {summary['lookback_days']}")
        sys.exit(1)
    if summary["servers_with_changes"] < 1:
        print(f"FAIL: expected >=1 servers with changes, got {summary['servers_with_changes']}")
        sys.exit(1)

    # --- 404 for server with no scores ---
    resp5 = client.get("/api/axis-changes/srv-probe-1", params={"lookback_days": 1000})
    # srv-probe-1 has scores, but 1000-day window should still work (returns results)
    if resp5.status_code != 200:
        print(f"FAIL: 1000-day window returned {resp5.status_code}: {resp5.text}")
        sys.exit(1)

    print("PASS")
