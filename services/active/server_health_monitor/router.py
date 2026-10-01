# deps: fastapi, pydantic, requests, sqlalchemy
"""FastAPI router for server health monitoring.

Combines:
  - App DB (McpServerRegistry, McpLlmAxisScore): scan/score freshness per server.
  - Write service (service_health DuckDB table): daemon heartbeat staleness.

Auth: public.  Prefix: /api.  Tag: server_health_monitor.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_health_monitor"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772"

# Freshness thresholds (days) for servers
SCAN_STALE_DAYS = 7
SCORE_STALE_DAYS = 7

# Daemon heartbeat thresholds (seconds) keyed by service name.
THRESHOLD_MAP: dict[str, int] = {
    "write_service": 300,
    "inference_router": 120,
    "manager_agent": 120,
    "pipeline_bridge": 120,
    "t2_consumer": 120,
    "zo_sentinel_builder": 600,
    "sentinel_directive_generator": 7500,
    "gate_scheduler": 60,
    "self_diagnostics": 600,
    "build_watcher_api": 600,
    "mcp_scanner": 14400,
    "signal_analyser": 120,
    "trust_synthesiser": 600,
    "threat_intel_ingestor": 600,
    "attestation_engine": 600,
    "rug_pull_monitor": 28800,
    "risk_ranker": 600,
    "world_article_feeder": 600,
    "data_velocity": 120,
    "anti_entropy": 14400,
    "wisdom_synthesiser": 14400,
    "gate_orchestrator": 14400,
}


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class ServerHealthEntry(BaseModel):
    server_id: str
    name: str
    last_scanned: Optional[str]
    last_assessed: Optional[str]
    days_since_scan: Optional[int]
    days_since_score: Optional[int]
    scan_status: str
    score_status: str
    overall_status: str


class DaemonHealthEntry(BaseModel):
    name: str
    last_heartbeat: str
    age_seconds: float
    status: str
    threshold_seconds: int


class ServerHealthMonitorResponse(BaseModel):
    total_servers: int
    fresh_servers: int
    stale_servers: int
    never_scored_servers: int
    servers: List[ServerHealthEntry]
    stale_daemons: List[DaemonHealthEntry]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _scan_status(last_scanned: Optional[datetime]) -> str:
    if last_scanned is None:
        return "never"
    age = (datetime.utcnow() - last_scanned).days
    return "stale" if age > SCAN_STALE_DAYS else "fresh"


def _score_status(last_assessed: Optional[datetime]) -> str:
    if last_assessed is None:
        return "never"
    age = (datetime.utcnow() - last_assessed).days
    return "stale" if age > SCORE_STALE_DAYS else "fresh"


def _overall_status(scan_st: str, score_st: str) -> str:
    if scan_st == "stale" or score_st == "stale":
        return "degraded"
    if scan_st == "never" and score_st == "never":
        return "unknown"
    return "healthy"


def _parse_timestamp(ts: str) -> datetime:
    """Parse ISO-8601 timestamp, handling Z suffix."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def _query_service_health() -> List[dict]:
    """Query write_service DuckDB service_health table."""
    payload = {"sql": "SELECT service, status, last_heartbeat FROM service_health", "params": []}
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to query write_service: {exc}")
    data = resp.json()
    rows = data.get("rows", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=500, detail="Malformed response from write_service")
    return rows


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/servers/health/monitor",
    response_model=ServerHealthMonitorResponse,
    name="server_health_monitor:get",
)
def server_health_monitor(
    db: Session = Depends(get_session),
) -> ServerHealthMonitorResponse:
    """
    Return combined server (scan/score freshness) and daemon (heartbeat staleness)
    health status.

    Server freshness thresholds: scan > 7 days stale, score > 7 days stale.
    Daemon staleness thresholds: defined per-service in THRESHOLD_MAP (default 300 s).
    """
    now_utc = datetime.utcnow()
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    # Latest score timestamp per server
    latest_score_subq = (
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
            latest_score_subq.c.last_assessed,
        )
        .outerjoin(latest_score_subq, McpServerRegistry.server_id == latest_score_subq.c.server_id)
        .all()
    )

    servers: List[ServerHealthEntry] = []
    fresh_servers = stale_servers = never_scored_servers = 0

    for r in rows:
        ls = _scan_status(r.last_scanned)
        ss = _score_status(r.last_assessed)
        ov = _overall_status(ls, ss)

        days_scan = (now_utc - r.last_scanned).days if r.last_scanned else None
        days_score = (now_utc - r.last_assessed).days if r.last_assessed else None

        servers.append(
            ServerHealthEntry(
                server_id=r.server_id,
                name=r.name or "",
                last_scanned=r.last_scanned.isoformat() if r.last_scanned else None,
                last_assessed=r.last_assessed.isoformat() if r.last_assessed else None,
                days_since_scan=days_scan,
                days_since_score=days_score,
                scan_status=ls,
                score_status=ss,
                overall_status=ov,
            )
        )

        if ov == "healthy":
            fresh_servers += 1
        elif ov == "degraded":
            stale_servers += 1
        else:
            never_scored_servers += 1

    # Daemon health from write_service
    stale_daemons: List[DaemonHealthEntry] = []
    try:
        health_rows = _query_service_health()
        for row in health_rows:
            name = row.get("service")
            raw_ts = row.get("last_heartbeat")
            if not name or not raw_ts:
                continue
            try:
                hb = _parse_timestamp(raw_ts)
            except Exception:
                continue
            age_seconds = (now_ts - hb).total_seconds()
            threshold = THRESHOLD_MAP.get(name, 300)
            if age_seconds > threshold:
                stale_daemons.append(
                    DaemonHealthEntry(
                        name=name,
                        last_heartbeat=raw_ts,
                        age_seconds=age_seconds,
                        status=row.get("status", "unknown"),
                        threshold_seconds=threshold,
                    )
                )
    except HTTPException:
        pass

    return ServerHealthMonitorResponse(
        total_servers=len(servers),
        fresh_servers=fresh_servers,
        stale_servers=stale_servers,
        never_scored_servers=never_scored_servers,
        servers=servers,
        stale_daemons=stale_daemons,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import subprocess, os

    _repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _result = subprocess.run(
        [
            "python3", "-c", f"""
import sys, os
sys.path.insert(0, {repr(_repo)})

import types
_stub_app = types.ModuleType('app')
_stub_db = types.ModuleType('app.db')
_stub_models = types.ModuleType('app.models')
sys.modules['app'] = _stub_app
sys.modules['app.db'] = _stub_db
sys.modules['app.models'] = _stub_models

# Stub session dependency
def _get_session():
    raise RuntimeError('test session not wired')
_stub_db.get_session = _get_session

# Real SQLAlchemy pieces for test DB
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, String, DateTime, create_engine
from sqlalchemy.pool import StaticPool
Base = declarative_base()

class McpServerRegistry(Base):
    __tablename__ = 'mcp_server_registry'
    server_id = Column(String, primary_key=True)
    name = Column(String)
    last_scanned = Column(DateTime, nullable=True)

class McpLlmAxisScore(Base):
    __tablename__ = 'mcp_llm_axis_scores'
    server_id = Column(String, primary_key=True)
    scored_at = Column(DateTime, nullable=True)

_stub_models.McpServerRegistry = McpServerRegistry
_stub_models.McpLlmAxisScore = McpLlmAxisScore
_stub_models.get_session = _get_session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

# Import router components
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
import requests

SCAN_STALE_DAYS = 7
SCORE_STALE_DAYS = 7

THRESHOLD_MAP = {{
    "write_service": 300,
    "inference_router": 120,
    "gate_scheduler": 60,
    "mcp_scanner": 14400,
    "signal_analyser": 120,
    "gate_orchestrator": 14400,
}}

class ServerHealthEntry(BaseModel):
    server_id: str; name: str; last_scanned: str = None; last_assessed: str = None
    days_since_scan: int = None; days_since_score: int = None
    scan_status: str; score_status: str; overall_status: str

class DaemonHealthEntry(BaseModel):
    name: str; last_heartbeat: str; age_seconds: float; status: str; threshold_seconds: int

class ServerHealthMonitorResponse(BaseModel):
    total_servers: int; fresh_servers: int; stale_servers: int
    never_scored_servers: int; servers: list; stale_daemons: list

router = APIRouter(prefix='/api', tags=['server_health_monitor'])

def _scan_status(last_scanned):
    if last_scanned is None: return 'never'
    return 'stale' if (datetime.utcnow() - last_scanned).days > SCAN_STALE_DAYS else 'fresh'

def _score_status(last_assessed):
    if last_assessed is None: return 'never'
    return 'stale' if (datetime.utcnow() - last_assessed).days > SCORE_STALE_DAYS else 'fresh'

def _overall_status(scan_st, score_st):
    if scan_st == 'stale' or score_st == 'stale': return 'degraded'
    if scan_st == 'never' and score_st == 'never': return 'unknown'
    return 'healthy'

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def _parse_timestamp(ts):
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)

def _query_service_health():
    payload = {{'sql': 'SELECT service, status, last_heartbeat FROM service_health', 'params': []}}
    resp = requests.post(f'{{WRITE_SERVICE_URL}}/query', json=payload, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])

@router.get('/servers/health/monitor', response_model=ServerHealthMonitorResponse)
def server_health_monitor(db: Session):
    now_utc = datetime.utcnow()
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    latest_score_subq = (
        db.query(McpLlmAxisScore.server_id, func.max(McpLlmAxisScore.scored_at).label('last_assessed'))
        .group_by(McpLlmAxisScore.server_id).subquery()
    )
    rows = (
        db.query(McpServerRegistry.server_id, McpServerRegistry.name, McpServerRegistry.last_scanned, latest_score_subq.c.last_assessed)
        .outerjoin(latest_score_subq, McpServerRegistry.server_id == latest_score_subq.c.server_id).all()
    )

    servers = []
    fresh_servers = stale_servers = never_scored_servers = 0
    for r in rows:
        ls = _scan_status(r.last_scanned)
        ss = _score_status(r.last_assessed)
        ov = _overall_status(ls, ss)
        days_scan = (now_utc - r.last_scanned).days if r.last_scanned else None
        days_score = (now_utc - r.last_assessed).days if r.last_assessed else None
        servers.append(ServerHealthEntry(
            server_id=r.server_id, name=r.name or '',
            last_scanned=r.last_scanned.isoformat() if r.last_scanned else None,
            last_assessed=r.last_assessed.isoformat() if r.last_assessed else None,
            days_since_scan=days_scan, days_since_score=days_score,
            scan_status=ls, score_status=ss, overall_status=ov,
        ))
        if ov == 'healthy': fresh_servers += 1
        elif ov == 'degraded': stale_servers += 1
        else: never_scored_servers += 1

    stale_daemons = []
    try:
        for row in _query_service_health():
            name = row.get('service'); raw_ts = row.get('last_heartbeat')
            if not name or not raw_ts: continue
            try: hb = _parse_timestamp(raw_ts)
            except: continue
            age_seconds = (now_ts - hb).total_seconds()
            threshold = THRESHOLD_MAP.get(name, 300)
            if age_seconds > threshold:
                stale_daemons.append(DaemonHealthEntry(
                    name=name, last_heartbeat=raw_ts, age_seconds=age_seconds,
                    status=row.get('status', 'unknown'), threshold_seconds=threshold,
                ))
    except Exception: pass

    return ServerHealthMonitorResponse(
        total_servers=len(servers), fresh_servers=fresh_servers, stale_servers=stale_servers,
        never_scored_servers=never_scored_servers, servers=servers, stale_daemons=stale_daemons,
    )

# Test DB setup
engine = create_engine('sqlite:///:memory:', connect_args={{'check_same_thread': False}}, poolclass=StaticPool)
Base.metadata.create_all(bind=engine)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
now = datetime.utcnow()

with SessionLocal() as db:
    db.add(McpServerRegistry(server_id='srv1', name='Fresh Server', last_scanned=now))
    db.add(McpServerRegistry(server_id='srv2', name='Stale Scan', last_scanned=now - timedelta(days=8)))
    db.add(McpServerRegistry(server_id='srv3', name='Never Scored', last_scanned=now))
    db.add(McpServerRegistry(server_id='srv4', name='All Stale', last_scanned=now - timedelta(days=10)))
    db.add(McpLlmAxisScore(server_id='srv1', scored_at=now))
    db.add(McpLlmAxisScore(server_id='srv2', scored_at=now))
    db.add(McpLlmAxisScore(server_id='srv4', scored_at=now - timedelta(days=10)))
    db.commit()

def _override():
    db = SessionLocal()
    try: yield db
    finally: db.close()

from fastapi import FastAPI
from fastapi.testclient import TestClient
app = FastAPI()
app.dependency_overrides[get_session] = _override
app.include_router(router)

# Fake write_service response
class _FakeResp:
    def raise_for_status(self): pass
    def json(self): return {{'rows': []}}

_orig_post = requests.post
requests.post = lambda *a, **kw: _FakeResp()

try:
    client = TestClient(app)
    resp = client.get('/api/servers/health/monitor')
    assert resp.status_code == 200, f'got {{resp.status_code}}'
    data = resp.json()
    assert len(data['servers']) == 4
    assert data['fresh_servers'] == 1, f'fresh={{data["fresh_servers"]}}'
    assert data['stale_servers'] == 2, f'stale={{data["stale_servers"]}}'
    assert data['never_scored_servers'] == 1, f'never={{data["never_scored_servers"]}}'
    print('PASS')
finally:
    requests.post = _orig_post
"""
        ],
        capture_output=True,
        text=True,
        cwd=_repo,
    )
    if _result.returncode == 0 and "PASS" in _result.stdout:
        print("PASS")
    else:
        print(_result.stdout, file=sys.stdout)
        print(_result.stderr, file=sys.stderr)
        sys.exit(_result.returncode)
