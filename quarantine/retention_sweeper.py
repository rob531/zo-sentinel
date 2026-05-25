#!/usr/bin/env python3
"""
Retention Sweeper Daemon - Enforces 30-day expiry on evidence_blob columns.
Reads from mcp_signal_scores and mcp_signal_enrichments via write_service.
"""

import time
import logging
import sys
import signal
import os
from datetime import datetime

import requests
import uvicorn
from fastapi import FastAPI

SERVICE_NAME = "retention_sweeper"
PORT = 8791
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
SERVICE_HEALTH_URL = "http://127.0.0.1:8772/write"
POLL_SECS = 3600
WRITE_TIMEOUT = 30
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI()
start_time = time.time()


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.info("Stale PID file removed")
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info(f"Acquired PID file: {pid}")


def send_heartbeat():
    try:
        requests.post(
            SERVICE_HEALTH_URL,
            json={"table": "service_health", "rows": {"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}},
            timeout=WRITE_TIMEOUT
        )
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def ws_query(sql):
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=WRITE_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log.error(f"Query failed: {e}")
        return None


def ws_write(table, rows):
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=WRITE_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log.error(f"Write failed: {e}")
        return None


def ws_execute(sql):
    try:
        response = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=WRITE_TIMEOUT
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log.error(f"Execute failed: {e}")
        return None


def delete_expired_from_signal_scores():
    sql = "DELETE FROM mcp_signal_scores WHERE recorded_at < NOW() - INTERVAL '30 days'"
    log.info("Deleting expired rows from mcp_signal_scores...")
    result = ws_execute(sql)
    if result:
        log.info(f"mcp_signal_scores cleanup completed: {result}")
    return result


def delete_expired_from_signal_enrichments():
    sql = "DELETE FROM mcp_signal_enrichments WHERE recorded_at < NOW() - INTERVAL '30 days'"
    log.info("Deleting expired rows from mcp_signal_enrichments...")
    result = ws_execute(sql)
    if result:
        log.info(f"mcp_signal_enrichments cleanup completed: {result}")
    return result


def get_record_counts():
    results = {}
    query_result = ws_query("SELECT COUNT(*) as count FROM mcp_signal_scores")
    if query_result and 'rows' in query_result:
        results['signal_scores_count'] = query_result['rows'][0]['count'] if query_result['rows'] else 0
    query_result = ws_query("SELECT COUNT(*) as count FROM mcp_signal_enrichments")
    if query_result and 'rows' in query_result:
        results['signal_enrichments_count'] = query_result['rows'][0]['count'] if query_result['rows'] else 0
    return results


def run():
    check_single_instance()
    
    def signal_handler(signum, frame):
        log.info(f"Received signal {signum}, shutting down gracefully...")
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Starting {SERVICE_NAME} daemon...")
    log.info(f"Retention policy: 30 days")
    log.info(f"Polling interval: {POLL_SECS}s")
    
    cycle_count = 0
    consecutive_failures = 0
    
    while True:
        cycle_start = time.time()
        cycle_count += 1
        log.info(f"=== Cycle {cycle_count} ===")
        
        try:
            send_heartbeat()
            
            before_counts = get_record_counts()
            log.info(f"Records before cleanup: {before_counts}")
            
            delete_expired_from_signal_scores()
            delete_expired_from_signal_enrichments()
            
            after_counts = get_record_counts()
            log.info(f"Records after cleanup: {after_counts}")
            
            deleted_signal_scores = before_counts.get('signal_scores_count', 0) - after_counts.get('signal_scores_count', 0)
            deleted_signal_enrichments = before_counts.get('signal_enrichments_count', 0) - after_counts.get('signal_enrichments_count', 0)
            
            log.info(f"Deleted {deleted_signal_scores} from signal_scores, {deleted_signal_enrichments} from signal_enrichments")
            
            consecutive_failures = 0
            
        except Exception as e:
            log.error(f"Error during cycle: {e}")
            consecutive_failures += 1
            if consecutive_failures >= 5:
                log.critical(f"Too many consecutive failures ({consecutive_failures}), stopping daemon")
                break
        
        cycle_duration = time.time() - cycle_start
        log.info(f"Cycle completed in {cycle_duration:.2f}s")
        
        sleep_time = max(0, POLL_SECS - cycle_duration)
        log.info(f"Sleeping for {sleep_time}s until next cycle")
        time.sleep(sleep_time)
    
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    log.info("Daemon stopped")


if __name__ == '__main__':
    run()
    uvicorn.run(app, host='127.0.0.1', port=PORT)


@app.get('/health')
def health():
    return {'status': 'ok', 'service': SERVICE_NAME, 'uptime': int(time.time() - start_time)}