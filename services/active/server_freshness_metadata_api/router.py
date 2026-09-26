# deps: fastapi, pydantic, requests
"""FastAPI router for server-freshness metadata.

Queries the app DB (McpServerRegistry, McpLlmAxisScore) for per-server
scan and axis-score freshness. Public access, no auth required.
"""
from __future__ import annotations

import os
import sys as _sys

# Must be first so that `from app.db / app.models` resolves at import time
_repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo not in _sys.path:
    _sys.path.insert(0, _repo)

from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["server_freshness_metadata_api"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Thresholds in seconds
_SCAN_FRESH_SEC = 300       # < 5 min  -> FRESH
_SCAN_STALE_SEC = 3600      # < 1 hr   -> STALE
_ASSESS_FRESH_SEC = 300     # < 5 min  -> CURRENT
_ASSESS_STALE_SEC = 3600    # < 1 hr   -> STALE


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class AxisFreshnessItem(BaseModel):
    axis_name: str
    scored_at: Optional[datetime]
    freshness_seconds: Optional[float]
    freshness_label: str


class ServerFreshnessResponse(BaseModel):
    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    scan_count: Optional[int]
    last_scanned: Optional[datetime]
    last_assessed: Optional[datetime]
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    freshness_seconds: Optional[float]
    assessment_staleness_seconds: Optional[float]
    freshness_label: str
    assessment_label: str
    axes: List[AxisFreshnessItem]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _scan_label(seconds: Optional[float]) -> str:
    if seconds is None:
        return "UNKNOWN"
    if seconds < _SCAN_FRESH_SEC:
        return "FRESH"
    if seconds < _SCAN_STALE_SEC:
        return "STALE"
    return "ARCHAIC"


def _assess_label(seconds: Optional[float]) -> str:
    if seconds is None:
        return "UNKNOWN"
    if seconds < _ASSESS_FRESH_SEC:
        return "CURRENT"
    if seconds < _ASSESS_STALE_SEC:
        return "STALE"
    return "ARCHAIC"


def _compute_freshness_seconds(ts: Optional[datetime]) -> Optional[float]:
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()


# --------------------------------------------------------------------------- #
# Endpoint logic
# --------------------------------------------------------------------------- #

def get_server_freshness(server_id: str, db: Session) -> ServerFreshnessResponse:
    srv = db.query(McpServerRegistry).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    freshness_seconds = _compute_freshness_seconds(srv.last_scanned)
    assessment_staleness_seconds = _compute_freshness_seconds(srv.last_assessed)

    subq = (
        db.query(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    axis_rows = (
        db.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
        )
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.latest_scored_at),
        )
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )

    axes: List[AxisFreshnessItem] = []
    for row in axis_rows:
        ax_fresh = _compute_freshness_seconds(row.scored_at)
        axes.append(AxisFreshnessItem(
            axis_name=row.axis_name,
            scored_at=row.scored_at,
            freshness_seconds=ax_fresh,
            freshness_label=_assess_label(ax_fresh),
        ))

    return ServerFreshnessResponse(
        server_id=srv.server_id,
        name=srv.name,
        url=srv.url,
        registry_source=srv.registry_source,
        scan_count=srv.scan_count,
        last_scanned=srv.last_scanned,
        last_assessed=srv.last_assessed,
        first_seen=srv.first_seen,
        last_seen=srv.last_seen,
        freshness_seconds=freshness_seconds,
        assessment_staleness_seconds=assessment_staleness_seconds,
        freshness_label=_scan_label(freshness_seconds),
        assessment_label=_assess_label(assessment_staleness_seconds),
        axes=axes,
    )


@router.get("/servers/{server_id}/freshness", response_model=ServerFreshnessResponse)
def server_freshness_endpoint(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerFreshnessResponse:
    """Return freshness metadata for a single server, including per-axis scores."""
    return get_server_freshness(server_id, db)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import subprocess

    _result = subprocess.run(
        ["python3", "-c", f"""
import sys, os
sys.path.insert(0, {repr(_repo)})

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime, timedelta, timezone

from app.models import Base, McpServerRegistry, McpLlmAxisScore
from app.db import get_session

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={{"check_same_thread": False}},
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

# Re-import router inside subprocess so it picks up the patched sys.path
# We re-define the router here to avoid import-order issues
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import func

_WRITE_SERVICE_URL = "http://127.0.0.1:8772"
_SCAN_FRESH_SEC = 300
_SCAN_STALE_SEC = 3600
_ASSESS_FRESH_SEC = 300
_ASSESS_STALE_SEC = 3600

class AxisFreshnessItem(BaseModel):
    axis_name: str
    scored_at: Optional[datetime]
    freshness_seconds: Optional[float]
    freshness_label: str

class ServerFreshnessResponse(BaseModel):
    server_id: str
    name: Optional[str]
    url: Optional[str]
    registry_source: Optional[str]
    scan_count: Optional[int]
    last_scanned: Optional[datetime]
    last_assessed: Optional[datetime]
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]
    freshness_seconds: Optional[float]
    assessment_staleness_seconds: Optional[float]
    freshness_label: str
    assessment_label: str
    axes: List[AxisFreshnessItem]

def _scan_label(seconds):
    if seconds is None: return "UNKNOWN"
    if seconds < _SCAN_FRESH_SEC: return "FRESH"
    if seconds < _SCAN_STALE_SEC: return "STALE"
    return "ARCHAIC"

def _assess_label(seconds):
    if seconds is None: return "UNKNOWN"
    if seconds < _ASSESS_FRESH_SEC: return "CURRENT"
    if seconds < _ASSESS_STALE_SEC: return "STALE"
    return "ARCHAIC"

def _compute_freshness_seconds(ts):
    if ts is None: return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds()

def get_server_freshness(server_id, db):
    srv = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not srv: raise HTTPException(status_code=404, detail="Server not found")
    freshness_seconds = _compute_freshness_seconds(srv.last_scanned)
    assessment_staleness_seconds = _compute_freshness_seconds(srv.last_assessed)

    subq = db.query(
        McpLlmAxisScore.axis_name,
        func.max(McpLlmAxisScore.scored_at).label("latest_scored_at"),
    ).filter(McpLlmAxisScore.server_id == server_id).group_by(McpLlmAxisScore.axis_name).subquery()

    axis_rows = db.query(McpLlmAxisScore.axis_name, McpLlmAxisScore.scored_at).join(
        subq,
        (McpLlmAxisScore.axis_name == subq.c.axis_name) & (McpLlmAxisScore.scored_at == subq.c.latest_scored_at),
    ).filter(McpLlmAxisScore.server_id == server_id).all()

    axes = []
    for row in axis_rows:
        ax_fresh = _compute_freshness_seconds(row.scored_at)
        axes.append(AxisFreshnessItem(
            axis_name=row.axis_name, scored_at=row.scored_at,
            freshness_seconds=ax_fresh, freshness_label=_assess_label(ax_fresh),
        ))

    return ServerFreshnessResponse(
        server_id=srv.server_id, name=srv.name, url=srv.url, registry_source=srv.registry_source,
        scan_count=srv.scan_count, last_scanned=srv.last_scanned, last_assessed=srv.last_assessed,
        first_seen=srv.first_seen, last_seen=srv.last_seen,
        freshness_seconds=freshness_seconds, assessment_staleness_seconds=assessment_staleness_seconds,
        freshness_label=_scan_label(freshness_seconds), assessment_label=_assess_label(assessment_staleness_seconds),
        axes=axes,
    )

test_router = APIRouter(prefix="/api", tags=["server_freshness_metadata_api"])

@test_router.get("/servers/{{server_id}}/freshness", response_model=ServerFreshnessResponse)
def endpoint(server_id: str, db=Depends(get_session)):
    return get_server_freshness(server_id, db)

app = FastAPI()
app.include_router(test_router)
app.dependency_overrides[get_session] = override_get_session

now = datetime.now(timezone.utc)
with TestingSession() as db:
    db.add(McpServerRegistry(
        server_id="srv-fresh", name="Fresh Server", url="https://fresh.example",
        registry_source="vendor", scan_count=5,
        last_scanned=now, last_assessed=now,
        first_seen=now - timedelta(days=30), last_seen=now,
    ))
    db.add(McpLlmAxisScore(
        id=1, server_id="srv-fresh", axis_name="overall_risk", label="low",
        label_index=1, scored_at=now, model_version="v1",
        adapter_sha256="abc", decision_rule_version="1",
    ))
    db.add(McpLlmAxisScore(
        id=2, server_id="srv-fresh", axis_name="auth_strength", label="medium",
        label_index=2, scored_at=now - timedelta(minutes=2), model_version="v1",
        adapter_sha256="abc", decision_rule_version="1",
    ))
    db.add(McpServerRegistry(
        server_id="srv-archaic", name="Old Server", url="https://old.example",
        registry_source="community", scan_count=2,
        last_scanned=now - timedelta(days=400), last_assessed=now - timedelta(days=400),
        first_seen=now - timedelta(days=500), last_seen=now - timedelta(days=400),
    ))
    db.add(McpLlmAxisScore(
        id=3, server_id="srv-archaic", axis_name="overall_risk", label="high",
        label_index=3, scored_at=now - timedelta(days=400), model_version="v1",
        adapter_sha256="def", decision_rule_version="1",
    ))
    db.add(McpServerRegistry(
        server_id="srv-never", name="Never Server", url="https://never.example",
        registry_source="community", scan_count=0,
        last_scanned=None, last_assessed=None,
        first_seen=now, last_seen=now,
    ))
    db.commit()

client = TestClient(app)

r1 = client.get("/api/servers/srv-fresh/freshness")
assert r1.status_code == 200, f"fresh got {{r1.status_code}}"
d1 = r1.json()
assert d1["freshness_label"] == "FRESH", f"fresh label: {{d1['freshness_label']}}"
assert d1["assessment_label"] == "CURRENT", f"assess label: {{d1['assessment_label']}}"
assert len(d1["axes"]) == 2, f"expected 2 axes, got {{len(d1['axes'])}}"

r2 = client.get("/api/servers/srv-archaic/freshness")
assert r2.status_code == 200, f"archaic got {{r2.status_code}}"
d2 = r2.json()
assert d2["freshness_label"] == "ARCHAIC", f"archaic label: {{d2['freshness_label']}}"
assert d2["assessment_label"] == "ARCHAIC"

r3 = client.get("/api/servers/srv-never/freshness")
assert r3.status_code == 200, f"never got {{r3.status_code}}"
d3 = r3.json()
assert d3["freshness_label"] == "UNKNOWN", f"never label: {{d3['freshness_label']}}"

r4 = client.get("/api/servers/not-found/freshness")
assert r4.status_code == 404, f"not-found got {{r4.status_code}}"

print("PASS")
"""],
        capture_output=True, text=True, cwd=_repo,
    )
    if _result.returncode == 0 and "PASS" in _result.stdout:
        print("PASS")
    else:
        print(_result.stdout, file=_sys.stdout)
        print(_result.stderr, file=_sys.stderr)
        _sys.exit(_result.returncode)
