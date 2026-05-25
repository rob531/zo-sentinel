import logging
import os
import sys
import signal
import time
import hashlib
from datetime import datetime, timezone, timedelta
import requests
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/signal_discrimination_validator.log')]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'signal_discrimination_validator'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = '/home/workspace/run/signal_discrimination_validator.pid'

_checked_pid = False

def check_single_instance():
    global _checked_pid
    if _checked_pid:
        return
    pid_dir = os.path.dirname(PID_FILE)
    if pid_dir and not os.path.exists(pid_dir):
        os.makedirs(pid_dir, exist_ok=True)
    if os.path.exists(PID_FILE):
        old_pid = int(open(PID_FILE).read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    _checked_pid = True

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)

def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql, params=None):
    payload = {'sql': sql, 'params': params if params else []}
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat(status='running', meta=None):
    ts = datetime.now(timezone.utc).isoformat()
    rows = {
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': ts,
        'meta': json.dumps(meta) if meta else None
    }
    ws_write('service_health', rows)

def compute_discrimination_cardinality():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    cutoff_iso = cutoff.isoformat()
    
    sql = """
    SELECT 
        signal_type,
        COUNT(DISTINCT enrichment_score) as distinct_scores,
        COUNT(*) as total_rows,
        MIN(enrichment_score) as min_score,
        MAX(enrichment_score) as max_score,
        AVG(enrichment_score) as avg_score
    FROM mcp_signal_enrichments
    WHERE enriched_at >= ?
      AND signal_type IN (
          'permission_scope_enrichment_v2',
          'temporal_stability_enrichment_v2',
          'tool_description_safety_enrichment_v2'
      )
    GROUP BY signal_type
    ORDER BY signal_type
    """
    result = ws_query(sql, [cutoff_iso])
    return result

def build_discrimination_report(cardinality_data):
    rows = []
    ts = datetime.now(timezone.utc).isoformat()
    
    for row in cardinality_data:
        signal_type = row.get('signal_type', 'unknown')
        distinct_scores = row.get('distinct_scores', 0)
        total_rows = row.get('total_rows', 0)
        min_score = row.get('min_score', 0.0)
        max_score = row.get('max_score', 0.0)
        
        discrimination_id = hashlib.sha256(
            f"{signal_type}:{ts}".encode()
        ).hexdigest()[:16]
        
        discrimination_row = {
            'validation_id': discrimination_id,
            'signal_type': signal_type,
            'distinct_score_count': distinct_scores,
            'total_enrichment_rows': total_rows,
            'score_min': float(min_score) if min_score is not None else 0.0,
            'score_max': float(max_score) if max_score is not None else 0.0,
            'validated_at': ts,
            'window_hours': 24
        }
        rows.append(discrimination_row)
    
    return rows

def cycle():
    logger.info("Starting signal discrimination validation cycle")
    meta = {'phase': 'cycle_start'}
    send_heartbeat('running', meta)
    
    cardinality_data = compute_discrimination_cardinality()
    
    if not cardinality_data:
        logger.warning("No enrichment data found in last 24h for target signal types")
        send_heartbeat('idle', {'phase': 'no_data', 'message': 'No recent enrichment data'})
        return
    
    logger.info(f"Found cardinality data for {len(cardinality_data)} signal types")
    
    discrimination_rows = build_discrimination_report(cardinality_data)
    ws_write('signal_discrimination_validation', discrimination_rows)
    
    meta = {
        'phase': 'complete',
        'signals_validated': len(cardinality_data),
        'rows_written': len(discrimination_rows)
    }
    send_heartbeat('running', meta)
    logger.info(f"Validation complete. Wrote {len(discrimination_rows)} discrimination rows")

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} starting up")
    
    POLL_SECS = 3600
    
    while True:
        cycle()
        logger.info(f"Sleeping {POLL_SECS}s before next cycle")
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()