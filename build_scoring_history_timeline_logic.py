import logging
import os
import sys
import time
import signal
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = 'scoring_history_timeline'
SERVICE_PORT = 8791
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
PID_FILE = '/tmp/scoring_history_timeline.pid'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = os.path.join(LOG_DIR, f'{SERVICE_NAME}.log')

POLL_SECS = 300
HISTORY_RETENTION_DAYS = 90
SIGNIFICANT_CHANGE_THRESHOLD = 5.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_write_url() -> str:
    return os.environ.get('WRITE_SERVICE_URL', WRITE_SERVICE_URL)


def get_query_url() -> str:
    return os.environ.get('QUERY_SERVICE_URL', QUERY_SERVICE_URL)


def get_execute_url() -> str:
    return os.environ.get('EXECUTE_SERVICE_URL', EXECUTE_SERVICE_URL)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    url = f"{get_write_url()}/write"
    try:
        resp = requests.post(url, json={'table': table, 'rows': rows}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for {table}: {e}")
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    url = get_query_url()
    try:
        resp = requests.post(url, json={'sql': sql}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_execute(sql: str) -> bool:
    url = get_execute_url()
    try:
        resp = requests.post(url, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def compute_deterministic_id(*fields: str) -> str:
    content = '|'.join(str(f) for f in fields)
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        old_pid = 0
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
        except:
            pass
        if old_pid and os.path.exists(f'/proc/{old_pid}'):
            log.error(f"Another instance running with PID {old_pid}")
            return False
        log.warning(f"Stale PID file found, removing")
        try:
            os.remove(PID_FILE)
        except:
            pass
    return True


def write_pid() -> None:
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log.error(f"Failed to write PID file: {e}")


def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.error(f"Failed to remove PID file: {e}")


def signal_handler(signum: int, frame: Any) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ensure_tables() -> None:
    create_score_history_sql = """
    CREATE TABLE IF NOT EXISTS mcp_score_history (
        history_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score DOUBLE,
        previous_score DOUBLE,
        score_change DOUBLE,
        evidence_summary VARCHAR,
        recorded_at TIMESTAMPTZ NOT NULL,
        UNIQUE(server_id, signal_name, recorded_at)
    )
    """
    create_trends_sql = """
    CREATE TABLE IF NOT EXISTS mcp_score_trends (
        trend_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        trend_direction VARCHAR,
        change_pct DOUBLE,
        from_score DOUBLE,
        to_score DOUBLE,
        period_start TIMESTAMPTZ,
        period_end TIMESTAMPTZ,
        computed_at TIMESTAMPTZ NOT NULL
    )
    """
    create_timeline_sql = """
    CREATE TABLE IF NOT EXISTS mcp_score_timeline (
        timeline_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score_series JSON,
        data_points INTEGER,
        avg_score DOUBLE,
        min_score DOUBLE,
        max_score DOUBLE,
        std_dev DOUBLE,
        period_start TIMESTAMPTZ,
        period_end TIMESTAMPTZ,
        computed_at TIMESTAMPTZ NOT NULL
    )
    """
    ws_execute(create_score_history_sql)
    ws_execute(create_trends_sql)
    ws_execute(create_timeline_sql)
    log.info("Ensured scoring history tables exist")


def send_heartbeat() -> None:
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'status': 'running',
        'ts': utc_now_iso(),
        'meta': f'polling={POLL_SECS}s'
    }])


def get_servers_with_signals() -> List[Dict[str, Any]]:
    sql = """
    SELECT DISTINCT server_id, name 
    FROM mcp_server_registry 
    WHERE server_id IS NOT NULL
    LIMIT 1000
    """
    return ws_query(sql)


def get_latest_scores_for_server(server_id: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT server_id, signal_name, score, evidence, computed_at
    FROM mcp_signal_scores
    WHERE server_id = '{server_id}'
    AND computed_at >= NOW() - INTERVAL '24 hours'
    ORDER BY computed_at DESC
    """
    return ws_query(sql)


def get_previous_history(server_id: str, signal_name: str, before_ts: str) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT score, recorded_at
    FROM mcp_score_history
    WHERE server_id = '{server_id}'
    AND signal_name = '{signal_name}'
    AND recorded_at < '{before_ts}'
    ORDER BY recorded_at DESC
    LIMIT 1
    """
    rows = ws_query(sql)
    return rows[0] if rows else None


def record_score_snapshot(server_id: str, signal_name: str, score: float, 
                          evidence_summary: str, computed_at: str) -> bool:
    previous = get_previous_history(server_id, signal_name, computed_at)
    previous_score = previous['score'] if previous else None
    score_change = score - previous_score if previous_score is not None else 0.0
    
    history_id = compute_deterministic_id(server_id, signal_name, computed_at)
    
    row = {
        'history_id': history_id,
        'server_id': server_id,
        'signal_name': signal_name,
        'score': score,
        'previous_score': previous_score,
        'score_change': score_change,
        'evidence_summary': evidence_summary[:500] if evidence_summary else None,
        'recorded_at': computed_at
    }
    
    return ws_write('mcp_score_history', [row])


def compute_trend_for_server(server_id: str, signal_name: str, 
                             period_days: int = 7) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT 
        score,
        recorded_at,
        LAG(score) OVER (ORDER BY recorded_at) as prev_score
    FROM mcp_score_history
    WHERE server_id = '{server_id}'
    AND signal_name = '{signal_name}'
    AND recorded_at >= NOW() - INTERVAL '{period_days} days'
    ORDER BY recorded_at ASC
    """
    history_rows = ws_query(sql)
    
    if not history_rows or len(history_rows) < 2:
        return None
    
    first_score = history_rows[0].get('score')
    last_score = history_rows[-1].get('score')
    
    if first_score is None or last_score is None:
        return None
    
    change_pct = ((last_score - first_score) / first_score * 100) if first_score != 0 else 0
    
    if change_pct > 10:
        direction = 'improving'
    elif change_pct < -10:
        direction = 'declining'
    else:
        direction = 'stable'
    
    trend_id = compute_deterministic_id(server_id, signal_name, 'trend', utc_now_iso())
    
    return {
        'trend_id': trend_id,
        'server_id': server_id,
        'signal_name': signal_name,
        'trend_direction': direction,
        'change_pct': change_pct,
        'from_score': first_score,
        'to_score': last_score,
        'period_start': history_rows[0]['recorded_at'],
        'period_end': history_rows[-1]['recorded_at'],
        'computed_at': utc_now_iso()
    }


def compute_timeline_stats(server_id: str, signal_name: str,
                           period_days: int = 30) -> Optional[Dict[str, Any]]:
    sql = f"""
    SELECT score, recorded_at
    FROM mcp_score_history
    WHERE server_id = '{server_id}'
    AND signal_name = '{signal_name}'
    AND recorded_at >= NOW() - INTERVAL '{period_days} days'
    ORDER BY recorded_at ASC
    """
    rows = ws_query(sql)
    
    if not rows:
        return None
    
    scores = [r['score'] for r in rows if r.get('score') is not None]
    if not scores:
        return None
    
    import statistics
    avg_score = statistics.mean(scores)
    min_score = min(scores)
    max_score = max(scores)
    std_dev = statistics.stdev(scores) if len(scores) > 1 else 0.0
    
    score_series = [{'ts': r['recorded_at'], 'score': r['score']} for r in rows]
    
    timeline_id = compute_deterministic_id(server_id, signal_name, 'timeline', utc_now_iso())
    
    return {
        'timeline_id': timeline_id,
        'server_id': server_id,
        'signal_name': signal_name,
        'score_series': str(score_series),
        'data_points': len(scores),
        'avg_score': avg_score,
        'min_score': min_score,
        'max_score': max_score,
        'std_dev': std_dev,
        'period_start': rows[0]['recorded_at'],
        'period_end': rows[-1]['recorded_at'],
        'computed_at': utc_now_iso()
    }


def detect_significant_changes(server_id: str) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        server_id,
        signal_name,
        score,
        previous_score,
        score_change,
        ABS(score_change) as abs_change,
        recorded_at
    FROM mcp_score_history
    WHERE server_id = '{server_id}'
    AND ABS(score_change) >= {SIGNIFICANT_CHANGE_THRESHOLD}
    AND recorded_at >= NOW() - INTERVAL '7 days'
    ORDER BY ABS(score_change) DESC
    """
    return ws_query(sql)


def get_server_timeline(server_id: str, signal_name: Optional[str] = None,
                        days: int = 30) -> List[Dict[str, Any]]:
    signal_filter = f"AND signal_name = '{signal_name}'" if signal_name else ""
    sql = f"""
    SELECT 
        server_id,
        signal_name,
        score,
        score_change,
        recorded_at
    FROM mcp_score_history
    WHERE server_id = '{server_id}'
    {signal_filter}
    AND recorded_at >= NOW() - INTERVAL '{days} days'
    ORDER BY recorded_at ASC
    """
    return ws_query(sql)


def cycle() -> int:
    log.info("Starting scoring history timeline cycle")
    processed = 0
    
    ensure_tables()
    
    servers = get_servers_with_signals()
    log.info(f"Processing {len(servers)} servers for score history")
    
    for server in servers:
        server_id = server.get('server_id')
        if not server_id:
            continue
        
        try:
            latest_scores = get_latest_scores_for_server(server_id)
            
            for score_row in latest_scores:
                signal_name = score_row.get('signal_name')
                score = score_row.get('score')
                computed_at = score_row.get('computed_at')
                evidence = score_row.get('evidence', '')
                
                if signal_name and score is not None and computed_at:
                    if record_score_snapshot(server_id, signal_name, score, evidence, computed_at):
                        processed += 1
            
            trend = compute_trend_for_server(server_id, 'trust_score', period_days=7)
            if trend:
                ws_write('mcp_score_trends', [trend])
            
            timeline = compute_timeline_stats(server_id, 'trust_score', period_days=30)
            if timeline:
                ws_write('mcp_score_timeline', [timeline])
            
            significant = detect_significant_changes(server_id)
            if significant:
                log.info(f"Detected {len(significant)} significant score changes for {server_id}")
                for change in significant:
                    log.info(f"  {change['signal_name']}: {change['previous_score']} -> {change['score']} "
                            f"(change: {change['score_change']:.2f})")
        
        except Exception as e:
            log.error(f"Error processing server {server_id}: {e}")
    
    log.info(f"Cycle complete. Processed {processed} score snapshots")
    return processed


def run() -> None:
    if not check_single_instance():
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    write_pid()
    log.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    
    ensure_tables()
    
    while True:
        try:
            cycle()
            send_heartbeat()
        except Exception as e:
            log.error(f"Error in main loop: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()