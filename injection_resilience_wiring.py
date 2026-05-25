import time
import logging
from datetime import datetime, timezone

SERVICE_NAME = "injection_resilience_wiring"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 300

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')
from injection_resilience_enrichment import compute_score

def check_single_instance():
    import os
    pid = str(os.getpid())
    try:
        with open(PID_FILE, 'r') as f:
            existing_pid = f.read().strip()
            if existing_pid and existing_pid != pid:
                import psutil
                if psutil.pid_exists(int(existing_pid)):
                    log.warning(f"Another instance running with PID {existing_pid}. Exiting.")
                    return False
    except FileNotFoundError:
        pass
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    return True

def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down gracefully")
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    sys.exit(0)

def ws_query(sql):
    import requests
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Query failed: {e}")
        return None

def ws_write(table, rows):
    import requests
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"Write failed: {e}")
        return None

def send_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": now}])

def get_servers_missing_injection_resilience():
    sql = """
    SELECT msr.server_id, msr.name, msr.url, msr.description
    FROM mcp_server_registry msr
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments mse
        WHERE mse.server_id = msr.server_id
        AND mse.signal_type = 'injection_resilience'
    )
    LIMIT 100
    """
    result = ws_query(sql)
    if result and result.get('rows'):
        return result['rows']
    return []

def compute_for_server(server):
    score_data = compute_score(server)
    return score_data

def write_enrichment(server_id, score_data):
    now = datetime.now(timezone.utc).isoformat()
    evidence_blob = {
        "prompt_variations_tested": score_data.get("prompt_variations_tested", 0),
        "successful_deflections": score_data.get("successful_deflections", 0),
        "deflection_rate": score_data.get("deflection_rate", 0.0),
        "context_boundary_enforcement": score_data.get("context_boundary_enforcement", False),
        "output_sanitization": score_data.get("output_sanitization", False),
        "token_limit_handling": score_data.get("token_limit_handling", False),
        "metadata_hash": score_data.get("metadata_hash", ""),
        "scoring_method": score_data.get("scoring_method", "static_analysis"),
        "computed_at": score_data.get("computed_at", now)
    }
    row = {
        "server_id": server_id,
        "signal_type": "injection_resilience",
        "score": score_data.get("score", 0.0),
        "confidence": score_data.get("confidence", 0.0),
        "evidence_blob": str(evidence_blob),
        "enriched_at": now
    }
    ws_write("mcp_signal_enrichments", [row])

def run():
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        return
    
    log.info(f"{SERVICE_NAME} started")
    send_heartbeat()
    
    while True:
        try:
            servers = get_servers_missing_injection_resilience()
            if servers:
                log.info(f"Processing {len(servers)} servers for injection_resilience enrichment")
                for server in servers:
                    try:
                        score_data = compute_for_server(server)
                        write_enrichment(server['server_id'], score_data)
                        log.info(f"Completed enrichment for server_id={server['server_id']}")
                    except Exception as e:
                        log.error(f"Failed to enrich server {server.get('server_id')}: {e}")
            else:
                log.info("No servers missing injection_resilience enrichment")
        except Exception as e:
            log.error(f"Cycle error: {e}")
        
        send_heartbeat()
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()