import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel, Field

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / 'axis_evidence_api.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger('axis_evidence_api')

SERVICE_NAME = 'axis_evidence_api'
PORT = 8796
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
POLL_SECS = 30
HEARTBEAT_INTERVAL = 60

app = FastAPI(title='AXIS Evidence API', version='1.0.0')

_process_start = datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error('ws_query failed: %s | SQL: %s', e, sql[:200])
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=15
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error('ws_write failed: %s | table=%s', e, table)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=15
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error('ws_execute failed: %s | SQL: %s', e, sql[:200])
        return False


def send_heartbeat(status: str = 'ok', meta: str = '') -> None:
    ts = utc_now_iso()
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': status,
        'meta': meta
    }
    ws_write('service_health', [row])


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error('Another instance already running with PID %s', old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            log.warning('Stale PID file %s, removing', old_pid)
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.warning('Could not remove PID file: %s', e)


def signal_handler(signum, frame) -> None:
    signame = signal.Signals(signum).name
    log.info('Received %s, shutting down gracefully', signame)
    remove_pid_file()
    sys.exit(0)


class EvidenceRecord(BaseModel):
    server_id: str = Field(..., description='MCP server ID this evidence belongs to')
    signal_type: str = Field(..., description='Signal type: attestation, threat, risk, audit, enrichment, compliance')
    evidence_id: str = Field(..., description='Deterministic evidence ID')
    content_hash: str = Field(..., description='SHA256 of evidence content')
    evidence_type: str = Field(..., description='Type: json, text, binary, score_record')
    content: str = Field(..., description='Evidence content (JSON string or text)')
    source_module: str = Field(..., description='Module that generated this evidence')
    source_uri: str = Field(default='', description='Original URI or reference')
    captured_at: str = Field(default_factory=utc_now_iso, description='When evidence was captured')
    expires_at: str = Field(default='', description='Optional expiry timestamp')
    tags: str = Field(default='', description='Comma-separated tags')
    metadata: str = Field(default='{}', description='JSON metadata blob')
    verified: bool = Field(default=False, description='Whether evidence has been verified')
    verification_note: str = Field(default='', description='Verification notes')


class EvidenceQuery(BaseModel):
    server_id: str | None = None
    signal_type: str | None = None
    evidence_type: str | None = None
    source_module: str | None = None
    verified_only: bool = False
    tag: str | None = None
    limit: int = Field(default=100, le=1000)
    offset: int = Field(default=0)


class EvidenceVerification(BaseModel):
    evidence_id: str
    verified: bool
    verification_note: str = ''
    verified_by: str = ''


class EvidenceRetention(BaseModel):
    evidence_id: str
    retention_days: int = Field(default=90, ge=1, le=3650)
    reason: str = ''


def ensure_evidence_table() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS axis_evidence (
        evidence_id VARCHAR PRIMARY KEY,
        server_id VARCHAR,
        signal_type VARCHAR,
        evidence_type VARCHAR,
        content_hash VARCHAR,
        content TEXT,
        source_module VARCHAR,
        source_uri VARCHAR,
        captured_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        tags VARCHAR,
        metadata JSON,
        verified BOOLEAN DEFAULT FALSE,
        verification_note VARCHAR,
        verified_by VARCHAR,
        verified_at TIMESTAMPTZ,
        retention_days INTEGER DEFAULT 90,
        retention_reason VARCHAR,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)

    index_sqls = [
        "CREATE INDEX IF NOT EXISTS idx_evidence_server_id ON axis_evidence(server_id)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_signal_type ON axis_evidence(signal_type)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_captured_at ON axis_evidence(captured_at)",
        "CREATE INDEX IF NOT EXISTS idx_evidence_content_hash ON axis_evidence(content_hash)",
    ]
    for idx_sql in index_sqls:
        ws_execute(idx_sql)


@app.on_event('startup')
async def startup():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info('AXIS Evidence API starting on port %s', PORT)
    ensure_evidence_table()
    send_heartbeat(status='ok', meta=f'port={PORT}')


@app.on_event('shutdown')
async def shutdown():
    remove_pid_file()
    log.info('AXIS Evidence API shutting down')


@app.get('/health')
async def health():
    uptime = (datetime.now(timezone.utc) - _process_start.replace(tzinfo=None)).total_seconds()
    return {'status': 'ok', 'service': SERVICE_NAME, 'uptime_seconds': round(uptime, 1)}


@app.post('/evidence', status_code=201)
async def create_evidence(record: EvidenceRecord):
    sql = """
    INSERT INTO axis_evidence (
        evidence_id, server_id, signal_type, evidence_type, content_hash,
        content, source_module, source_uri, captured_at, expires_at, tags,
        metadata, verified, verification_note
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (evidence_id) DO UPDATE SET
        content = EXCLUDED.content,
        metadata = EXCLUDED.metadata,
        updated_at = CURRENT_TIMESTAMP
    """
    now = utc_now_iso()
    success = ws_execute(sql)
    if not success:
        raise HTTPException(status_code=500, detail='Failed to write evidence record')

    send_heartbeat(status='ok', meta=f'action=create_evidence,evidence_id={record.evidence_id}')
    return {'status': 'created', 'evidence_id': record.evidence_id}


@app.post('/evidence/bulk', status_code=201)
async def create_evidence_bulk(records: list[EvidenceRecord]):
    if len(records) > 500:
        raise HTTPException(status_code=400, detail='Bulk insert limited to 500 records per request')
    if not records:
        raise HTTPException(status_code=400, detail='No records provided')

    now = utc_now_iso()
    rows = []
    for r in records:
        rows.append({
            'evidence_id': r.evidence_id,
            'server_id': r.server_id,
            'signal_type': r.signal_type,
            'evidence_type': r.evidence_type,
            'content_hash': r.content_hash,
            'content': r.content,
            'source_module': r.source_module,
            'source_uri': r.source_uri,
            'captured_at': r.captured_at or now,
            'expires_at': r.expires_at or '',
            'tags': r.tags or '',
            'metadata': r.metadata or '{}',
            'verified': r.verified,
            'verification_note': r.verification_note or '',
        })

    success = ws_write('axis_evidence', rows)
    if not success:
        raise HTTPException(status_code=500, detail='Failed to bulk write evidence records')

    send_heartbeat(status='ok', meta=f'action=bulk_create_evidence,count={len(records)}')
    return {'status': 'created', 'count': len(records)}


@app.get('/evidence/{evidence_id}')
async def get_evidence(evidence_id: str):
    sql = f"SELECT * FROM axis_evidence WHERE evidence_id = ? LIMIT 1"
    rows = ws_query(f"SELECT * FROM axis_evidence WHERE evidence_id = '{evidence_id}' LIMIT 1")
    if not rows:
        raise HTTPException(status_code=404, detail=f'Evidence {evidence_id} not found')
    return rows[0]


@app.get('/evidence')
async def list_evidence(
    server_id: str | None = Query(None),
    signal_type: str | None = Query(None),
    evidence_type: str | None = Query(None),
    source_module: str | None = Query(None),
    verified_only: bool = Query(False),
    tag: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    conditions = []
    params = {}

    if server_id:
        conditions.append(f"server_id = '{server_id}'")
    if signal_type:
        conditions.append(f"signal_type = '{signal_type}'")
    if evidence_type:
        conditions.append(f"evidence_type = '{evidence_type}'")
    if source_module:
        conditions.append(f"source_module = '{source_module}'")
    if verified_only:
        conditions.append("verified = TRUE")
    if tag:
        conditions.append(f"tags LIKE '%{tag}%'")

    where_clause = ' AND '.join(conditions) if conditions else '1=1'
    sql = f"SELECT * FROM axis_evidence WHERE {where_clause} ORDER BY captured_at DESC LIMIT {limit} OFFSET {offset}"

    rows = ws_query(sql)
    count_rows = ws_query(f"SELECT COUNT(*) as cnt FROM axis_evidence WHERE {where_clause}")

    total = count_rows[0]['cnt'] if count_rows else 0
    return {'rows': rows, 'total': total, 'limit': limit, 'offset': offset}


@app.patch('/evidence/{evidence_id}/verify')
async def verify_evidence(evidence_id: str, verification: EvidenceVerification):
    now = utc_now_iso()
    sql = f"""
    UPDATE axis_evidence SET
        verified = {verification.verified},
        verification_note = '{verification.verification_note}',
        verified_by = '{verification.verified_by}',
        verified_at = '{now}',
        updated_at = '{now}'
    WHERE evidence_id = '{evidence_id}'
    """
    success = ws_execute(sql)
    if not success:
        raise HTTPException(status_code=500, detail='Failed to update verification status')

    send_heartbeat(status='ok', meta=f'action=verify_evidence,evidence_id={evidence_id}')
    return {'status': 'updated', 'evidence_id': evidence_id, 'verified': verification.verified}


@app.post('/evidence/{evidence_id}/retention')
async def set_retention(evidence_id: str, retention: EvidenceRetention):
    now = utc_now_iso()
    sql = f"""
    UPDATE axis_evidence SET
        retention_days = {retention.retention_days},
        retention_reason = '{retention.reason}',
        updated_at = '{now}'
    WHERE evidence_id = '{evidence_id}'
    """
    success = ws_execute(sql)
    if not success:
        raise HTTPException(status_code=500, detail='Failed to set retention policy')

    send_heartbeat(status='ok', meta=f'action=set_retention,evidence_id={evidence_id},days={retention.retention_days}')
    return {'status': 'updated', 'evidence_id': evidence_id, 'retention_days': retention.retention_days}


@app.delete('/evidence/{evidence_id}')
async def delete_evidence(evidence_id: str):
    sql = f"DELETE FROM axis_evidence WHERE evidence_id = '{evidence_id}'"
    success = ws_execute(sql)
    if not success:
        raise HTTPException(status_code=500, detail='Failed to delete evidence record')

    send_heartbeat(status='ok', meta=f'action=delete_evidence,evidence_id={evidence_id}')
    return {'status': 'deleted', 'evidence_id': evidence_id}


@app.get('/evidence/stats/overview')
async def evidence_stats():
    sql_total = ws_query("SELECT COUNT(*) as total FROM axis_evidence")
    sql_verified = ws_query("SELECT COUNT(*) as verified FROM axis_evidence WHERE verified = TRUE")
    sql_by_signal = ws_query("""
        SELECT signal_type, COUNT(*) as count, COUNT(CASE WHEN verified THEN 1 END) as verified_count
        FROM axis_evidence
        GROUP BY signal_type
        ORDER BY count DESC
    """)
    sql_by_source = ws_query("""
        SELECT source_module, COUNT(*) as count
        FROM axis_evidence
        GROUP BY source_module
        ORDER BY count DESC
        LIMIT 20
    """)
    sql_by_type = ws_query("""
        SELECT evidence_type, COUNT(*) as count
        FROM axis_evidence
        GROUP BY evidence_type
        ORDER BY count DESC
    """)
    sql_expiring = ws_query(f"""
        SELECT COUNT(*) as expiring
        FROM axis_evidence
        WHERE expires_at IS NOT NULL AND expires_at != ''
        AND expires_at < '{utc_now_iso()}'
    """)

    return {
        'total': sql_total[0]['total'] if sql_total else 0,
        'verified': sql_verified[0]['verified'] if sql_verified else 0,
        'by_signal_type': sql_by_signal,
        'by_source_module': sql_by_source,
        'by_evidence_type': sql_by_type,
        'expiring_count': sql_expiring[0]['expiring'] if sql_expiring else 0,
        'as_of': utc_now_iso()
    }


@app.get('/evidence/stats/server/{server_id}')
async def server_evidence_stats(server_id: str):
    sql = ws_query(f"""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN verified THEN 1 END) as verified,
            signal_type,
            MIN(captured_at) as first_evidence,
            MAX(captured_at) as last_evidence
        FROM axis_evidence
        WHERE server_id = '{server_id}'
        GROUP BY signal_type
        ORDER BY total DESC
    """)
    summary_rows = ws_query(f"SELECT COUNT(*) as total FROM axis_evidence WHERE server_id = '{server_id}'")
    return {
        'server_id': server_id,
        'signal_breakdown': sql,
        'total': summary_rows[0]['total'] if summary_rows else 0,
        'as_of': utc_now_iso()
    }


@app.get('/evidence/hash/{content_hash}')
async def get_evidence_by_hash(content_hash: str):
    rows = ws_query(f"SELECT * FROM axis_evidence WHERE content_hash = '{content_hash}'")
    return {'rows': rows, 'count': len(rows)}


@app.get('/evidence/timeline/{server_id}')
async def evidence_timeline(
    server_id: str,
    limit: int = Query(200, ge=1, le=2000)
):
    rows = ws_query(f"""
        SELECT evidence_id, signal_type, evidence_type, captured_at,
               verified, source_module, tags
        FROM axis_evidence
        WHERE server_id = '{server_id}'
        ORDER BY captured_at ASC
        LIMIT {limit}
    """)
    return {'server_id': server_id, 'timeline': rows, 'count': len(rows)}


@app.get('/evidence/related/{evidence_id}')
async def get_related_evidence(evidence_id: str, limit: int = Query(10, ge=1, le=50)):
    base = ws_query(f"SELECT server_id, signal_type, content_hash FROM axis_evidence WHERE evidence_id = '{evidence_id}' LIMIT 1")
    if not base:
        raise HTTPException(status_code=404, detail='Evidence not found')

    server_id = base[0]['server_id']
    signal_type = base[0]['signal_type']
    rows = ws_query(f"""
        SELECT * FROM axis_evidence
        WHERE server_id = '{server_id}'
          AND signal_type = '{signal_type}'
          AND evidence_id != '{evidence_id}'
        ORDER BY captured_at DESC
        LIMIT {limit}
    """)
    return {'base_evidence_id': evidence_id, 'related': rows, 'count': len(rows)}


def run():
    log.info('Starting AXIS Evidence API daemon on port %s', PORT)
    send_heartbeat(status='starting')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')


if __name__ == '__main__':
    run()