#!/usr/bin/env python3
"""
ZO-SENTINEL PI Scorer
Computes injection_resilience signal (0-100) from pi_test_results aggregated per MCP server.
Threshold 0.80 = BLOCKING. Triggers verdict recalculation on threshold breach.
"""

import os
import sys
import time
import logging
import signal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import requests

SERVICE_NAME = 'pi_scorer'
PORT = 8791
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
QUERY_URL = 'http://127.0.0.1:8772/query'
TRUST_SYNTHESIS_URL = 'http://127.0.0.1:8783/trigger_recalculate'

POLL_SECS = 600
HEARTBEAT_INTERVAL = 300
BLOCKING_THRESHOLD = 0.80
LOG_DIR = '/home/workspace/zo_sentinel/logs'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(name)s] %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_db_path() -> str:
    return '/home/workspace/zo_sentinel/data/sentinel.db'


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed to {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Execute failed: {sql[:100]}... Error: {e}")
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Service already running with PID {old_pid}")
            return False
        except OSError:
            logger.warning(f"Stale PID file found for PID {old_pid}")
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat() -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={
            'table': 'service_health',
            'rows': {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()},
            'wait': True
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False


def ensure_pi_signal_table() -> bool:
    sql = """
    CREATE SEQUENCE IF NOT EXISTS pi_signal_id_seq
    """
    ws_execute(sql)
    
    sql = """
    CREATE TABLE IF NOT EXISTS pi_signal_scores (
        id              BIGINT PRIMARY KEY DEFAULT nextval('pi_signal_id_seq'),
        server_id       VARCHAR NOT NULL,
        test_passed     INTEGER,
        test_failed     INTEGER,
        test_total      INTEGER,
        pass_rate       REAL,
        injection_score REAL,
        risk_level      VARCHAR,
        computed_at     TIMESTAMPTZ DEFAULT now()
    )
    """
    ws_execute(sql)
    
    sql = """
    CREATE TABLE IF NOT EXISTS pi_threshold_breaches (
        id              BIGINT PRIMARY KEY DEFAULT nextval('pi_signal_id_seq'),
        server_id       VARCHAR NOT NULL,
        pass_rate       REAL,
        threshold       REAL,
        risk_level      VARCHAR,
        breach_at       TIMESTAMPTZ DEFAULT now(),
        verdict_triggered BOOLEAN DEFAULT FALSE
    )
    """
    ws_execute(sql)
    return True


def get_pi_test_results() -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        COUNT(*) as test_total,
        SUM(CASE WHEN result = 'PASS' OR result = 'pass' OR result = 'passed' THEN 1 ELSE 0 END) as test_passed,
        SUM(CASE WHEN result = 'FAIL' OR result = 'fail' OR result = 'failed' THEN 1 ELSE 0 END) as test_failed,
        AVG(CAST(score AS REAL)) as avg_score
    FROM pi_test_results
    WHERE server_id IS NOT NULL
    GROUP BY server_id
    HAVING COUNT(*) > 0
    """
    return ws_query(sql)


def compute_injection_resilience(pass_rate: float, avg_score: Optional[float] = None) -> float:
    if pass_rate is None:
        return 0.0
    
    base_score = pass_rate * 100
    
    if avg_score is not None:
        adjustment = (avg_score - 0.5) * 20
        base_score = base_score + adjustment
    
    return max(0.0, min(100.0, base_score))


def get_risk_level(injection_score: float) -> str:
    if injection_score >= 80:
        return 'LOW'
    elif injection_score >= 60:
        return 'MEDIUM'
    elif injection_score >= 40:
        return 'HIGH'
    else:
        return 'CRITICAL'


def trigger_verdict_recalculation(server_id: str) -> bool:
    try:
        resp = requests.post(TRUST_SYNTHESIS_URL, json={'server_id': server_id, 'triggered_by': 'pi_scorer'}, timeout=30)
        if resp.status_code == 200:
            logger.info(f"Triggered verdict recalculation for {server_id}")
            return True
        else:
            logger.warning(f"Failed to trigger recalculation for {server_id}: {resp.status_code}")
            return False
    except Exception as e:
        logger.warning(f"Could not trigger recalculation for {server_id}: {e}")
        return False


def update_signal_scores(server_id: str, injection_score: float, evidence: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    
    rows = [{
        'server_id': server_id,
        'signal_name': 'injection_resilience',
        'score': injection_score,
        'evidence': evidence,
        'scored_at': now
    }]
    
    return ws_write('mcp_signal_scores', rows)


def record_pi_signal(server_id: str, test_passed: int, test_failed: int, test_total: int, 
                     pass_rate: float, injection_score: float, risk_level: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    
    rows = [{
        'server_id': server_id,
        'test_passed': test_passed,
        'test_failed': test_failed,
        'test_total': test_total,
        'pass_rate': pass_rate,
        'injection_score': injection_score,
        'risk_level': risk_level,
        'computed_at': now
    }]
    
    return ws_write('pi_signal_scores', rows)


def record_threshold_breach(server_id: str, pass_rate: float, threshold: float, risk_level: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    
    rows = [{
        'server_id': server_id,
        'pass_rate': pass_rate,
        'threshold': threshold,
        'risk_level': risk_level,
        'breach_at': now,
        'verdict_triggered': False
    }]
    
    return ws_write('pi_threshold_breaches', rows)


def mark_breach_triggered(server_id: str, breach_at: str) -> bool:
    sql = f"""
    UPDATE pi_threshold_breaches 
    SET verdict_triggered = TRUE 
    WHERE server_id = '{server_id}' AND breach_at = '{breach_at}'
    """
    return ws_execute(sql)


def process_servers() -> Tuple[int, int]:
    results = get_pi_test_results()
    processed = 0
    breaches = 0
    
    for row in results:
        server_id = row.get('server_id')
        if not server_id:
            continue
        
        test_total = row.get('test_total', 0) or 0
        test_passed = row.get('test_passed', 0) or 0
        test_failed = row.get('test_failed', 0) or 0
        avg_score = row.get('avg_score')
        
        if test_total == 0:
            continue
        
        pass_rate = test_passed / test_total if test_total > 0 else 0.0
        injection_score = compute_injection_resilience(pass_rate, avg_score)
        risk_level = get_risk_level(injection_score)
        
        evidence = f"PI tests: {test_passed}/{test_total} passed ({pass_rate*100:.1f}%), avg_score={avg_score:.3f if avg_score else 'N/A'}"
        
        update_signal_scores(server_id, injection_score, evidence)
        record_pi_signal(server_id, test_passed, test_failed, test_total, pass_rate, injection_score, risk_level)
        
        if pass_rate < BLOCKING_THRESHOLD:
            breaches += 1
            logger.warning(f"THRESHOLD BREACH: {server_id} pass_rate={pass_rate:.3f} < {BLOCKING_THRESHOLD}")
            record_threshold_breach(server_id, pass_rate, BLOCKING_THRESHOLD, risk_level)
            trigger_verdict_recalculation(server_id)
        
        processed += 1
    
    return processed, breaches


def cycle():
    logger.info("Starting PI scoring cycle")
    ensure_pi_signal_table()
    
    processed, breaches = process_servers()
    
    logger.info(f"PI scoring cycle complete: processed={processed}, threshold_breaches={breaches}")
    return processed, breaches


def run():
    if not check_single_instance():
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} starting on port {PORT}")
    
    start_time = time.time()
    last_heartbeat = 0
    cycle_count = 0
    
    ensure_pi_signal_table()
    
    try:
        while True:
            try:
                cycle()
                cycle_count += 1
            except Exception as e:
                logger.error(f"Error in cycle: {e}", exc_info=True)
            
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL:
                send_heartbeat()
                last_heartbeat = now
            
            time.sleep(POLL_SECS)
    except Exception as e:
        logger.error(f"Fatal error in run loop: {e}", exc_info=True)
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()