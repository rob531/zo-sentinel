import time
import logging
import signal
import os
from datetime import datetime, timedelta
import requests

SERVICE_NAME = "verdict_aggregator_daemon"
PORT = 8795
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
SERVICE_PORT = 8795
POLL_SECS = 1800
VERDICT_HYSTERESIS_HOURS = 6

LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
LOG_DIR = "/tmp"
for d in [LOG_DIR]:
    os.makedirs(d, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger(SERVICE_NAME)

SIGNAL_WEIGHTS = {
    "tool_description_safety": 0.20,
    "injection_resilience": 0.18,
    "supply_chain_trust": 0.15,
    "temporal_stability": 0.15,
    "permission_scope": 0.12,
    "domain_trust": 0.10,
    "community_signal": 0.10,
}

VERDICT_THRESHOLDS = {
    "TRUSTED": 0.80,
    "CAUTION": 0.50,
    "REVIEW": 0.25,
}

verdict_lock = False

def log(msg):
    LOG.info(msg)

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing = f.read().strip()
        if existing and existing.isdigit():
            existing_pid = int(existing)
            try:
                os.kill(existing_pid, 0)
                if existing_pid != pid:
                    log(f"Instance already running with PID {existing_pid}, exiting.")
                    return False
            except OSError:
                log(f"Stale PID file found (PID {existing_pid}), removing.")
                with open(PID_FILE, 'w') as f:
                    f.write(str(pid))
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True

def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass

def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    exit(0)

def get_write_url():
    return WRITE_SERVICE_URL

def get_query_url():
    return QUERY_SERVICE_URL

def ws_query(sql):
    try:
        resp = requests.post(get_query_url(), json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_query error: {e}")
        return {"rows": [], "count": 0}

def ws_write(rows, table):
    try:
        resp = requests.post(get_write_url(), json={"table": table, "rows": rows, "wait": True}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_write error: {e}")
        raise

def ws_execute(sql):
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={"sql": sql}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_execute error: {e}")
        raise

def compute_aggregate_score(signal_scores):
    weighted_sum = 0.0
    total_weight = 0.0
    for signal_name, weight in SIGNAL_WEIGHTS.items():
        score = signal_scores.get(signal_name)
        if score is not None:
            try:
                score_val = float(score)
                weighted_sum += score_val * weight
                total_weight += weight
            except (ValueError, TypeError):
                pass
    if total_weight == 0:
        return 0.0
    return weighted_sum / sum(SIGNAL_WEIGHTS.values())

def score_to_verdict(score):
    if score >= VERDICT_THRESHOLDS["TRUSTED"]:
        return "TRUSTED"
    elif score >= VERDICT_THRESHOLDS["CAUTION"]:
        return "CAUTION"
    elif score >= VERDICT_THRESHOLDS["REVIEW"]:
        return "REVIEW"
    else:
        return "BLOCKED"

def get_servers_needing_verdict_update():
    cutoff = datetime.utcnow() - timedelta(hours=VERDICT_HYSTERESIS_HOURS)
    sql = f"""
        SELECT 
            r.server_id,
            r.name,
            COALESCE(r.verdict, 'UNKNOWN') as current_verdict,
            r.verdict_updated_at,
            r.trust_score
        FROM mcp_server_registry r
        WHERE r.verdict_updated_at IS NULL 
           OR r.verdict_updated_at < '{cutoff.isoformat()}'
        ORDER BY r.server_id
    """
    result = ws_query(sql)
    return result.get("rows", [])

def get_signal_scores_for_server(server_id):
    sql = f"""
        SELECT signal_name, score
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
    """
    result = ws_query(sql)
    signal_scores = {}
    for row in result.get("rows", []):
        signal_scores[row.get("signal_name")] = row.get("score")
    return signal_scores

def emit_mesh_event(event_type, counts):
    now = datetime.utcnow().isoformat()
    payload = {
        "event_type": event_type,
        "timestamp": now,
        "counts": counts,
        "service": SERVICE_NAME
    }
    try:
        ws_write([payload], "mesh_events")
        log(f"Emitted mesh_event: {event_type} with counts: {counts}")
    except Exception as e:
        log(f"Failed to emit mesh_event: {e}")

def send_heartbeat():
    try:
        ws_write([{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat(),
            "status": "running"
        }], "service_health")
    except Exception as e:
        log(f"Heartbeat failed: {e}")

def update_verdict(server_id, new_verdict, new_trust_score):
    sql = f"""
        UPDATE mcp_server_registry
        SET 
            verdict = '{new_verdict}',
            trust_score = {new_trust_score},
            verdict_updated_at = '{datetime.utcnow().isoformat()}'
        WHERE server_id = '{server_id}'
    """
    ws_execute(sql)

def ensure_mesh_events_table():
    sql = """
        CREATE TABLE IF NOT EXISTS mesh_events (
            event_type VARCHAR,
            timestamp TIMESTAMP,
            payload JSON,
            event_id IDENTITY
        )
    """
    try:
        ws_execute(sql)
    except Exception as e:
        log(f"Table ensure (may exist): {e}")

def cycle():
    log(f"Starting verdict aggregation cycle at {datetime.utcnow().isoformat()}")
    ensure_mesh_events_table()
    
    servers = get_servers_needing_verdict_update()
    total_servers = len(servers)
    updated_count = 0
    skipped_count = 0
    error_count = 0
    
    log(f"Found {total_servers} servers needing verdict update")
    
    for server in servers:
        server_id = server.get("server_id")
        current_verdict = server.get("current_verdict")
        trust_score = server.get("trust_score")
        
        try:
            signal_scores = get_signal_scores_for_server(server_id)
            
            if not signal_scores:
                log(f"Server {server_id}: No signal scores found, skipping")
                skipped_count += 1
                continue
            
            aggregate_score = compute_aggregate_score(signal_scores)
            new_verdict = score_to_verdict(aggregate_score)
            
            log(f"Server {server_id} ({server.get('name')}): score={aggregate_score:.3f}, verdict={new_verdict} (was {current_verdict})")
            
            update_verdict(server_id, new_verdict, aggregate_score)
            updated_count += 1
            
        except Exception as e:
            log(f"Error processing server {server_id}: {e}")
            error_count += 1
    
    counts = {
        "total_servers_processed": total_servers,
        "updated": updated_count,
        "skipped": skipped_count,
        "errors": error_count,
        "cycle_time_utc": datetime.utcnow().isoformat()
    }
    
    emit_mesh_event("verdict_aggregation", counts)
    
    log(f"Cycle complete: {updated_count} updated, {skipped_count} skipped, {error_count} errors")

def run():
    if not check_single_instance():
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log(f"{SERVICE_NAME} starting, PID={os.getpid()}")
    log(f"Signal weights: {SIGNAL_WEIGHTS}")
    log(f"Verdict thresholds: {VERDICT_THRESHOLDS}")
    log(f"Verdict hysteresis: {VERDICT_HYSTERESIS_HOURS} hours")
    log(f"Polling interval: {POLL_SECS} seconds")
    
    while True:
        try:
            send_heartbeat()
            cycle()
        except Exception as e:
            log(f"Cycle error: {e}")
        
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    run()