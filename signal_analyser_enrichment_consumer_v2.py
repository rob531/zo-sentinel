import time
import signal
import os
import json
import requests
from datetime import datetime, timezone

SERVICE_NAME = "signal_analyser_enrichment_consumer"
SERVICE_PORT = 8791
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/tmp/{SERVICE_NAME}.log"
POLL_SECS = 30
HEARTBEAT_INTERVAL = 30

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"

ENRICHMENT_SIGNAL_TYPES = [
    'temporal_stability_enrichment',
    'tool_description_safety_enrichment',
    'permission_scope_enrichment'
]

started_at = None

def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_write_url():
    return WRITE_URL

def get_query_url():
    return QUERY_URL

def get_execute_url():
    return EXECUTE_URL

def ws_query(sql):
    try:
        resp = requests.post(get_query_url(), json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_query error: {e}")
        return None

def ws_write(table, rows, wait=True):
    try:
        payload = {"table": table, "rows": rows, "wait": wait}
        resp = requests.post(get_write_url(), json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_write error: {e}")
        return None

def ws_execute(sql):
    try:
        resp = requests.post(get_execute_url(), json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"ws_execute error: {e}")
        return None

def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log(f"Another instance running as PID {old_pid}, exiting.")
            return False
        except OSError:
            log(f"Stale PID file found for {old_pid}")
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True

def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass

def signal_handler(signum, frame):
    log(f"Received signal {signum}, shutting down.")
    remove_pid_file()
    exit(0)

def send_heartbeat():
    global started_at
    uptime = int(time.time() - started_at) if started_at else 0
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": datetime.now(timezone.utc).isoformat()})

def get_unscored_enriched_servers():
    sql = f"""
    SELECT DISTINCT server_id
    FROM mcp_signal_enrichments
    WHERE signal_type IN ('temporal_stability_enrichment', 'tool_description_safety_enrichment', 'permission_scope_enrichment')
    AND server_id NOT IN (
        SELECT server_id FROM mcp_signal_scores 
        WHERE signal_name IN ('temporal_stability', 'tool_description_safety', 'permission_scope')
        AND scored_at > CURRENT_TIMESTAMP - INTERVAL '1 hour'
    )
    LIMIT 50
    """
    result = ws_query(sql)
    if result and result.get("rows"):
        return result["rows"]
    return []

def get_enrichment_data_for_server(server_id):
    sql = f"""
    SELECT signal_type, score, evidence, enriched_at
    FROM mcp_signal_enrichments
    WHERE server_id = '{server_id}'
    AND signal_type IN ('temporal_stability_enrichment', 'tool_description_safety_enrichment', 'permission_scope_enrichment')
    ORDER BY enriched_at DESC
    """
    result = ws_query(sql)
    if result and result.get("rows"):
        return result["rows"]
    return []

def map_enrichment_to_signal(signal_type):
    mapping = {
        'temporal_stability_enrichment': 'temporal_stability',
        'tool_description_safety_enrichment': 'tool_description_safety',
        'permission_scope_enrichment': 'permission_scope'
    }
    return mapping.get(signal_type, signal_type)

def compute_derived_score(enrichment_score, signal_name, evidence_data):
    base_score = enrichment_score if enrichment_score is not None else 0.5
    if signal_name == 'temporal_stability':
        if 'age_days' in evidence_data:
            age_days = float(evidence_data.get('age_days', 0))
            if age_days > 730:
                base_score = min(1.0, base_score + 0.1)
            elif age_days < 30:
                base_score = max(0.0, base_score - 0.15)
    elif signal_name == 'tool_description_safety':
        safety_terms = ['read', 'write', 'delete', 'execute', 'admin', 'system']
        desc = evidence_data.get('description', '').lower()
        risk_count = sum(1 for t in safety_terms if t in desc)
        if risk_count >= 4:
            base_score = max(0.0, base_score - 0.2)
        elif risk_count <= 1:
            base_score = min(1.0, base_score + 0.1)
    elif signal_name == 'permission_scope':
        perm_count = evidence_data.get('permission_count', 0)
        if perm_count > 20:
            base_score = max(0.0, base_score - 0.15)
        elif perm_count <= 5:
            base_score = min(1.0, base_score + 0.1)
    return max(0.0, min(1.0, base_score))

def parse_evidence(evidence_str):
    if not evidence_str:
        return {}
    try:
        if isinstance(evidence_str, str):
            return json.loads(evidence_str)
        return evidence_str if isinstance(evidence_str, dict) else {}
    except Exception:
        return {}

def write_signal_score(server_id, signal_name, score, evidence):
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "server_id": server_id,
        "signal_name": signal_name,
        "score": round(score, 4),
        "evidence": json.dumps(evidence) if isinstance(evidence, dict) else evidence,
        "scored_at": now
    }
    result = ws_write("mcp_signal_scores", row)
    return result

def process_server(server_id):
    log(f"Processing server: {server_id}")
    enrichments = get_enrichment_data_for_server(server_id)
    if not enrichments:
        log(f"No enrichments found for {server_id}")
        return 0
    processed = 0
    for enrich in enrichments:
        signal_type = enrich.get("signal_type", "")
        if signal_type not in ENRICHMENT_SIGNAL_TYPES:
            continue
        signal_name = map_enrichment_to_signal(signal_type)
        enrichment_score = enrich.get("score")
        evidence_raw = enrich.get("evidence", "{}")
        evidence_data = parse_evidence(evidence_raw)
        final_score = compute_derived_score(enrichment_score, signal_name, evidence_data)
        evidence_data['enrichment_source'] = signal_type
        evidence_data['enrichment_score'] = enrichment_score
        evidence_data['derived_score'] = final_score
        write_signal_score(server_id, signal_name, final_score, evidence_data)
        processed += 1
        log(f"Wrote signal '{signal_name}' score {final_score:.4f} for {server_id}")
    return processed

def ensure_consumer_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichment_consumer_log (
        id INTEGER DEFAULT AUTOINCREMENT,
        server_id VARCHAR,
        signals_processed INTEGER,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)

def log_consumer_batch(server_count, signals_count):
    row = {
        "server_id": f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "signals_processed": signals_count,
        "processed_at": datetime.now(timezone.utc).isoformat()
    }
    ws_write("mcp_signal_enrichment_consumer_log", row)

def cycle():
    log("Starting enrichment consumer cycle")
    ensure_consumer_table()
    servers = get_unscored_enriched_servers()
    total_signals = 0
    for server_row in servers:
        server_id = server_row.get("server_id") if isinstance(server_row, dict) else server_row
        if server_id:
            count = process_server(server_id)
            total_signals += count
    log(f"Cycle complete: processed {len(servers)} servers, {total_signals} signals")
    log_consumer_batch(len(servers), total_signals)
    send_heartbeat()

def run():
    global started_at
    log("Starting signal_analyser_enrichment_consumer")
    if not check_single_instance():
        return
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    started_at = time.time()
    ensure_consumer_table()
    send_heartbeat()
    log(f"Daemon running on port {SERVICE_PORT}, PID {os.getpid()}")
    while True:
        try:
            cycle()
        except Exception as e:
            log(f"Error in cycle: {e}")
        time.sleep(POLL_SECS)

if __name__ == "__main__":
    run()