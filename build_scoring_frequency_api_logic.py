import logging
import os
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/scoring_frequency_api_logic.log')]
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'scoring_frequency_api_logic'
PORT = 8791
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
PID_FILE = '/tmp/scoring_frequency_api_logic.pid'
POLL_SECS = 30

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
sys.path.insert(0, str(PROJECT_DIR))


def ws_query(sql: str) -> list:
    payload = {'sql': sql}
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: list) -> None:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def ws_execute(sql: str) -> None:
    payload = {'sql': sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    return Path(os.environ.get('DUCKDB_PATH', '/home/workspace/Datasets/zo-sentinel/sentinel.db'))


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file from {old_pid}. Removing.")
            pid_file.unlink()
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame) -> None:
    log.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def ensure_tables() -> None:
    create_scoring_frequency_sql = """
    CREATE TABLE IF NOT EXISTS scoring_frequency (
        id INTEGER PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        scoring_cycle VARCHAR NOT NULL,
        frequency_count INTEGER DEFAULT 0,
        last_scored_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
    """
    create_scoring_events_sql = """
    CREATE TABLE IF NOT EXISTS scoring_events (
        id INTEGER PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        scored_at TIMESTAMPTZ NOT NULL,
        signal_type VARCHAR,
        score_value DOUBLE,
        meta JSON
    )
    """
    create_scoring_stats_sql = """
    CREATE TABLE IF NOT EXISTS scoring_stats (
        id INTEGER PRIMARY KEY,
        stat_date DATE NOT NULL,
        total_scored INTEGER DEFAULT 0,
        unique_servers INTEGER DEFAULT 0,
        avg_score DOUBLE,
        computed_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
    )
    """
    try:
        ws_execute(create_scoring_frequency_sql)
        ws_execute(create_scoring_events_sql)
        ws_execute(create_scoring_stats_sql)
        log.info("Scoring frequency tables ensured.")
    except Exception as e:
        log.warning(f"Table creation warning (may already exist): {e}")


def get_scoring_frequency(server_id: str = None, cycle: str = None) -> dict:
    conditions = []
    params = {}
    if server_id:
        conditions.append("server_id = ?")
        params['server_id'] = server_id
    if cycle:
        conditions.append("scoring_cycle = ?")
        params['cycle'] = cycle
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM scoring_frequency WHERE {where_clause} ORDER BY updated_at DESC LIMIT 100"
    
    try:
        rows = ws_query(sql)
        return {'servers': rows, 'count': len(rows)}
    except Exception as e:
        log.error(f"Failed to query scoring frequency: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_scoring_events(server_id: str = None, event_type: str = None, limit: int = 100) -> dict:
    conditions = []
    if server_id:
        conditions.append(f"server_id = '{server_id}'")
    if event_type:
        conditions.append(f"event_type = '{event_type}'")
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM scoring_events WHERE {where_clause} ORDER BY scored_at DESC LIMIT {limit}"
    
    try:
        rows = ws_query(sql)
        return {'events': rows, 'count': len(rows)}
    except Exception as e:
        log.error(f"Failed to query scoring events: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_daily_stats(date_str: str = None) -> dict:
    if date_str:
        sql = f"SELECT * FROM scoring_stats WHERE stat_date = '{date_str}' ORDER BY computed_at DESC LIMIT 1"
    else:
        sql = "SELECT * FROM scoring_stats ORDER BY stat_date DESC LIMIT 30"
    
    try:
        rows = ws_query(sql)
        return {'stats': rows, 'count': len(rows)}
    except Exception as e:
        log.error(f"Failed to query scoring stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def record_scoring_event(server_id: str, event_type: str, signal_type: str = None, 
                         score_value: float = None, meta: dict = None) -> dict:
    event_id = abs(hash(f"{server_id}{event_type}{utc_now_iso()}")) % (10**12)
    row = {
        'id': event_id,
        'server_id': server_id,
        'event_type': event_type,
        'scored_at': utc_now_iso(),
        'signal_type': signal_type,
        'score_value': score_value,
        'meta': meta
    }
    try:
        ws_write('scoring_events', [row])
        update_frequency(server_id, event_type)
        return {'success': True, 'event_id': event_id}
    except Exception as e:
        log.error(f"Failed to record scoring event: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def update_frequency(server_id: str, cycle: str) -> None:
    now = utc_now_iso()
    existing = ws_query(f"SELECT * FROM scoring_frequency WHERE server_id = '{server_id}' AND scoring_cycle = '{cycle}'")
    
    if existing:
        current_count = existing[0].get('frequency_count', 0) + 1
        update_sql = f"""
        UPDATE scoring_frequency 
        SET frequency_count = {current_count}, 
            last_scored_at = '{now}',
            updated_at = '{now}'
        WHERE server_id = '{server_id}' AND scoring_cycle = '{cycle}'
        """
    else:
        row = {
            'server_id': server_id,
            'scoring_cycle': cycle,
            'frequency_count': 1,
            'last_scored_at': now,
            'updated_at': now
        }
        ws_write('scoring_frequency', [row])
        return
    
    try:
        ws_execute(update_sql)
    except Exception as e:
        log.warning(f"Frequency update failed (may be duplicate key): {e}")


def compute_daily_stats() -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    sql = f"""
    SELECT 
        COUNT(DISTINCT server_id) as unique_servers,
        COUNT(*) as total_scored,
        AVG(score_value) as avg_score
    FROM scoring_events 
    WHERE DATE(scored_at) = '{today}'
    """
    
    try:
        rows = ws_query(sql)
        if rows:
            row = rows[0]
            stat_id = abs(hash(f"{today}stats")) % (10**12)
            stat_row = {
                'id': stat_id,
                'stat_date': today,
                'total_scored': row.get('total_scored', 0) or 0,
                'unique_servers': row.get('unique_servers', 0) or 0,
                'avg_score': row.get('avg_score') or 0.0,
                'computed_at': utc_now_iso()
            }
            ws_write('scoring_stats', [stat_row])
            return {'success': True, 'stats': stat_row}
        return {'success': False, 'message': 'No data for today'}
    except Exception as e:
        log.error(f"Failed to compute daily stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_frequency_summary() -> dict:
    sql = """
    SELECT 
        scoring_cycle,
        COUNT(DISTINCT server_id) as server_count,
        SUM(frequency_count) as total_events,
        AVG(frequency_count) as avg_frequency
    FROM scoring_frequency
    GROUP BY scoring_cycle
    ORDER BY scoring_cycle
    """
    
    try:
        rows = ws_query(sql)
        return {'summary': rows, 'count': len(rows)}
    except Exception as e:
        log.error(f"Failed to get frequency summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


app = FastAPI()


@app.get('/health')
def health():
    return {'status': 'ok', 'service': SERVICE_NAME, 'timestamp': utc_now_iso()}


@app.get('/api/scoring/frequency')
def api_get_frequency(server_id: str = None, cycle: str = None):
    return get_scoring_frequency(server_id, cycle)


@app.get('/api/scoring/events')
def api_get_events(server_id: str = None, event_type: str = None, limit: int = 100):
    return get_scoring_events(server_id, event_type, limit)


@app.get('/api/scoring/stats')
def api_get_stats(date: str = None):
    return get_daily_stats(date)


@app.get('/api/scoring/summary')
def api_get_summary():
    return get_frequency_summary()


@app.post('/api/scoring/event')
def api_record_event(server_id: str, event_type: str, signal_type: str = None,
                     score_value: float = None, meta: dict = None):
    return record_scoring_event(server_id, event_type, signal_type, score_value, meta)


@app.post('/api/scoring/compute-stats')
def api_compute_stats():
    return compute_daily_stats()


def send_heartbeat() -> None:
    try:
        row = {
            'service': SERVICE_NAME,
            'last_heartbeat': utc_now_iso(),
            'status': 'running',
            'meta': {'port': PORT}
        }
        ws_write('service_health', [row])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(POLL_SECS)


def run() -> None:
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    
    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')


if __name__ == '__main__':
    run()