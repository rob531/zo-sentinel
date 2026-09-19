import logging
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import hashlib
import json

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/perspective_snapshot_api.log')]
)
log = logging.getLogger('perspective_snapshot_api')

SERVICE_NAME = 'perspective_snapshot_api'
PORT = 8796
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
PID_FILE = '/tmp/perspective_snapshot_api.pid'

app = FastAPI(title='Perspective Snapshot API', version='1.0.0')

# ---- Pydantic Models ----

class SnapshotSignalScore(BaseModel):
    signal_name: str
    score: float
    evidence: Optional[str] = None

class PerspectiveSnapshotCreate(BaseModel):
    server_id: str
    snapshot_label: Optional[str] = None
    snapshot_reason: Optional[str] = None

class PerspectiveSnapshot(BaseModel):
    snapshot_id: str
    server_id: str
    snapshot_label: Optional[str]
    snapshot_reason: Optional[str]
    trust_score: float
    verdict: str
    risk_tier: str
    signal_scores: List[SnapshotSignalScore]
    created_at: str

class SnapshotListResponse(BaseModel):
    snapshots: List[PerspectiveSnapshot]
    count: int

class TimelinePoint(BaseModel):
    ts: str
    trust_score: float
    verdict: str

class TimelineResponse(BaseModel):
    server_id: str
    points: List[TimelinePoint]

class ComparisonSnapshot(BaseModel):
    snapshot_id: str
    trust_score: float
    verdict: str
    ts: str

class ComparisonResponse(BaseModel):
    server_id_a: str
    server_id_b: str
    snapshot_a: ComparisonSnapshot
    snapshot_b: ComparisonSnapshot
    score_delta: float
    verdict_change: str

# ---- Write Service Helpers ----

def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        import requests
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f'ws_query failed: {sql[:100]} -- {e}')
        return []

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        import requests
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f'ws_write failed for {table}: {e}')
        return False

def ws_execute(sql: str) -> bool:
    try:
        import requests
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f'ws_execute failed: {sql[:100]} -- {e}')
        return False

# ---- Deterministic ID ----

def compute_snapshot_id(server_id: str, ts: str, label: Optional[str] = None) -> str:
    raw = f'{server_id}:{ts}:{label or ""}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

# ---- Ensure Table ----

def ensure_perspective_snapshots_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS perspective_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        snapshot_label VARCHAR,
        snapshot_reason VARCHAR,
        trust_score DOUBLE,
        verdict VARCHAR,
        risk_tier VARCHAR,
        signal_scores_json VARCHAR,
        created_at TIMESTAMPTZ
    )
    """
    ws_execute(sql)

# ---- Core Logic ----

def get_current_signals_for_server(server_id: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT signal_name, score, evidence
    FROM mcp_signal_scores
    WHERE server_id = '{server_id.replace("'", "''")}'
    ORDER BY signal_name
    """
    return ws_query(sql)

def get_server_registry(server_id: str) -> Dict[str, Any]:
    sql = f"""
    SELECT server_id, name, trust_score, verdict, risk_tier
    FROM mcp_server_registry
    WHERE server_id = '{server_id.replace("'", "''")}'
    LIMIT 1
    """
    rows = ws_query(sql)
    return rows[0] if rows else {}

def get_trust_score_history(server_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT ts, trust_score, verdict
    FROM trust_score_time_series
    WHERE server_id = '{server_id.replace("'", "''")}'
    ORDER BY ts DESC
    LIMIT {limit}
    """
    return ws_query(sql)

def compute_trust_from_signals(signal_rows: List[Dict[str, Any]]) -> float:
    if not signal_rows:
        return 0.0
    total = sum(float(r.get('score', 0)) for r in signal_rows)
    count = len(signal_rows)
    return round(total / count, 4) if count > 0 else 0.0

def create_perspective_snapshot(server_id: str, label: Optional[str] = None, reason: Optional[str] = None) -> PerspectiveSnapshot:
    now_iso = datetime.now(timezone.utc).isoformat()
    snapshot_id = compute_snapshot_id(server_id, now_iso, label)

    registry = get_server_registry(server_id)
    signal_rows = get_current_signals_for_server(server_id)

    trust_score = float(registry.get('trust_score', 0.0)) if registry else compute_trust_from_signals(signal_rows)
    verdict = registry.get('verdict', 'UNKNOWN') if registry else 'UNKNOWN'
    risk_tier = registry.get('risk_tier', 'UNKNOWN') if registry else 'UNKNOWN'

    signal_scores = [
        SnapshotSignalScore(
            signal_name=r['signal_name'],
            score=float(r.get('score', 0)),
            evidence=r.get('evidence')
        )
        for r in signal_rows
    ]

    row = {
        'snapshot_id': snapshot_id,
        'server_id': server_id,
        'snapshot_label': label,
        'snapshot_reason': reason,
        'trust_score': trust_score,
        'verdict': verdict,
        'risk_tier': risk_tier,
        'signal_scores_json': json.dumps([s.model_dump() for s in signal_scores]),
        'created_at': now_iso
    }
    ws_write('perspective_snapshots', [row])

    log.info(f'Created perspective snapshot {snapshot_id} for {server_id} trust={trust_score}')

    return PerspectiveSnapshot(
        snapshot_id=snapshot_id,
        server_id=server_id,
        snapshot_label=label,
        snapshot_reason=reason,
        trust_score=trust_score,
        verdict=verdict,
        risk_tier=risk_tier,
        signal_scores=signal_scores,
        created_at=now_iso
    )

def load_snapshot(snapshot_id: str) -> Optional[PerspectiveSnapshot]:
    sql = f"""
    SELECT snapshot_id, server_id, snapshot_label, snapshot_reason,
           trust_score, verdict, risk_tier, signal_scores_json, created_at
    FROM perspective_snapshots
    WHERE snapshot_id = '{snapshot_id.replace("'", "''")}'
    LIMIT 1
    """
    rows = ws_query(sql)
    if not rows:
        return None
    r = rows[0]
    try:
        sig_json = json.loads(r.get('signal_scores_json', '[]'))
    except Exception:
        sig_json = []
    signal_scores = [SnapshotSignalScore(**s) for s in sig_json]
    return PerspectiveSnapshot(
        snapshot_id=r['snapshot_id'],
        server_id=r['server_id'],
        snapshot_label=r.get('snapshot_label'),
        snapshot_reason=r.get('snapshot_reason'),
        trust_score=float(r.get('trust_score', 0)),
        verdict=r.get('verdict', 'UNKNOWN'),
        risk_tier=r.get('risk_tier', 'UNKNOWN'),
        signal_scores=signal_scores,
        created_at=r['created_at']
    )

def list_snapshots(
    server_id: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
) -> SnapshotListResponse:
    conditions = []
    if server_id:
        conditions.append(f"server_id = '{server_id.replace("'", "''")}'")
    if from_ts:
        conditions.append(f"created_at >= '{from_ts}'")
    if to_ts:
        conditions.append(f"created_at <= '{to_ts}'")
    where_clause = ' AND '.join(conditions) if conditions else '1=1'

    count_sql = f"SELECT COUNT(*) as cnt FROM perspective_snapshots WHERE {where_clause}"
    count_rows = ws_query(count_sql)
    total = int(count_rows[0]['cnt']) if count_rows else 0

    sql = f"""
    SELECT snapshot_id, server_id, snapshot_label, snapshot_reason,
           trust_score, verdict, risk_tier, signal_scores_json, created_at
    FROM perspective_snapshots
    WHERE {where_clause}
    ORDER BY created_at DESC
    LIMIT {limit} OFFSET {offset}
    """
    rows = ws_query(sql)

    snapshots = []
    for r in rows:
        try:
            sig_json = json.loads(r.get('signal_scores_json', '[]'))
        except Exception:
            sig_json = []
        signal_scores = [SnapshotSignalScore(**s) for s in sig_json]
        snapshots.append(PerspectiveSnapshot(
            snapshot_id=r['snapshot_id'],
            server_id=r['server_id'],
            snapshot_label=r.get('snapshot_label'),
            snapshot_reason=r.get('snapshot_reason'),
            trust_score=float(r.get('trust_score', 0)),
            verdict=r.get('verdict', 'UNKNOWN'),
            risk_tier=r.get('risk_tier', 'UNKNOWN'),
            signal_scores=signal_scores,
            created_at=r['created_at']
        ))

    return SnapshotListResponse(snapshots=snapshots, count=total)

def build_timeline(server_id: str, limit: int = 100) -> TimelineResponse:
    points = []
    rows = get_trust_score_history(server_id, limit)
    for r in rows:
        points.append(TimelinePoint(
            ts=r.get('ts', ''),
            trust_score=float(r.get('trust_score', 0)),
            verdict=r.get('verdict', 'UNKNOWN')
        ))
    return TimelineResponse(server_id=server_id, points=points)

def compare_snapshots(snapshot_id_a: str, snapshot_id_b: str) -> ComparisonResponse:
    snap_a = load_snapshot(snapshot_id_a)
    snap_b = load_snapshot(snapshot_id_b)
    if not snap_a:
        raise HTTPException(status_code=404, detail=f'Snapshot {snapshot_id_a} not found')
    if not snap_b:
        raise HTTPException(status_code=404, detail=f'Snapshot {snapshot_id_b} not found')

    score_delta = round(snap_b.trust_score - snap_a.trust_score, 4)
    verdict_change = 'unchanged'
    if snap_a.verdict != snap_b.verdict:
        verdict_change = f'{snap_a.verdict} -> {snap_b.verdict}'

    return ComparisonResponse(
        server_id_a=snap_a.server_id,
        server_id_b=snap_b.server_id,
        snapshot_a=ComparisonSnapshot(
            snapshot_id=snap_a.snapshot_id,
            trust_score=snap_a.trust_score,
            verdict=snap_a.verdict,
            ts=snap_a.created_at
        ),
        snapshot_b=ComparisonSnapshot(
            snapshot_id=snap_b.snapshot_id,
            trust_score=snap_b.trust_score,
            verdict=snap_b.verdict,
            ts=snap_b.created_at
        ),
        score_delta=score_delta,
        verdict_change=verdict_change
    )

# ---- FastAPI Routes ----

@app.get('/health')
def health():
    return {'status': 'ok', 'service': SERVICE_NAME}

@app.post('/api/perspective-snapshots', response_model=PerspectiveSnapshot)
def create_snapshot(body: PerspectiveSnapshotCreate):
    ensure_perspective_snapshots_table()
    return create_perspective_snapshot(body.server_id, body.snapshot_label, body.snapshot_reason)

@app.get('/api/perspective-snapshots', response_model=SnapshotListResponse)
def list_perspective_snapshots(
    server_id: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None, description='ISO 8601 start bound'),
    to_ts: Optional[str] = Query(None, description='ISO 8601 end bound'),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    return list_snapshots(server_id, from_ts, to_ts, limit, offset)

@app.get('/api/perspective-snapshots/{snapshot_id}', response_model=PerspectiveSnapshot)
def get_snapshot(snapshot_id: str):
    snap = load_snapshot(snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail='Snapshot not found')
    return snap

@app.get('/api/perspective-snapshots/{server_id}/timeline', response_model=TimelineResponse)
def get_timeline(server_id: str, limit: int = Query(100, ge=1, le=1000)):
    return build_timeline(server_id, limit)

@app.get('/api/perspective-snapshots/compare/{snapshot_id_a}/{snapshot_id_b}', response_model=ComparisonResponse)
def compare_two(snapshot_id_a: str, snapshot_id_b: str):
    return compare_snapshots(snapshot_id_a, snapshot_id_b)

@app.get('/api/perspective-snapshots/{server_id}/latest', response_model=PerspectiveSnapshot)
def get_latest(server_id: str):
    sql = f"""
    SELECT snapshot_id, server_id, snapshot_label, snapshot_reason,
           trust_score, verdict, risk_tier, signal_scores_json, created_at
    FROM perspective_snapshots
    WHERE server_id = '{server_id.replace("'", "''")}'
    ORDER BY created_at DESC
    LIMIT 1
    """
    rows = ws_query(sql)
    if not rows:
        raise HTTPException(status_code=404, detail=f'No snapshot found for {server_id}')
    r = rows[0]
    try:
        sig_json = json.loads(r.get('signal_scores_json', '[]'))
    except Exception:
        sig_json = []
    signal_scores = [SnapshotSignalScore(**s) for s in sig_json]
    return PerspectiveSnapshot(
        snapshot_id=r['snapshot_id'],
        server_id=r['server_id'],
        snapshot_label=r.get('snapshot_label'),
        snapshot_reason=r.get('snapshot_reason'),
        trust_score=float(r.get('trust_score', 0)),
        verdict=r.get('verdict', 'UNKNOWN'),
        risk_tier=r.get('risk_tier', 'UNKNOWN'),
        signal_scores=signal_scores,
        created_at=r['created_at']
    )

# ---- Startup ----

@app.on_event('startup')
def startup():
    log.info(f'{SERVICE_NAME} starting on port {PORT}')
    ensure_perspective_snapshots_table()

def run():
    import signal
    def handler(signum, frame):
        log.info(f'{SERVICE_NAME} received signal {signum}, shutting down')
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')

if __name__ == '__main__':
    run()