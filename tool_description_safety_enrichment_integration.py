import logging
import os
import signal
import sys
import time
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/tool_description_safety_enrichment_integration.log')]
)
log = logging.getLogger(__name__)

SERVICE_NAME = "tool_description_safety_enrichment_integration"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
HEARTBEAT_INTERVAL = 60
BATCH_SIZE = 100

def ws_query(sql: str, params: tuple = None) -> Dict[str, Any]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def ws_execute(sql: str, params: tuple = None) -> Dict[str, Any]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows}
    response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def check_single_instance():
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            existing_pid = f.read().strip()
        if existing_pid and existing_pid != pid:
            try:
                os.kill(int(existing_pid), 0)
                log.error(f"Another instance already running with PID {existing_pid}")
                sys.exit(1)
            except OSError:
                log.warning(f"Stale PID file found, overwriting")
    with open(PID_FILE, 'w') as f:
        f.write(pid)

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)

def send_heartbeat():
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": ts,
        "status": "running",
        "ts": ts,
        "meta": json.dumps({"batch_size": BATCH_SIZE})
    }
    try:
        ws_write("service_health", [row])
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")

def compute_score(metadata: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from tool_description_safety_enrichment_v2 import compute_score as v2_compute
        return v2_compute(metadata)
    except ImportError as e:
        log.error(f"Cannot import v2 module: {e}")
        return {"score": 0.0, "confidence": 0.0, "evidence": {}}

def compute_deterministic_id(server_id: str, signal_type: str) -> str:
    raw = f"{server_id}:{signal_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def get_unscored_servers() -> List[Dict[str, Any]]:
    sql = """
    SELECT DISTINCT r.server_id, r.name, r.description
    FROM mcp_server_registry r
    WHERE NOT EXISTS (
        SELECT 1 FROM mcp_signal_enrichments e
        WHERE e.server_id = r.server_id
        AND e.signal_type = 'tool_description_safety'
    )
    LIMIT ?
    """
    try:
        result = ws_query(sql, params=(BATCH_SIZE,))
        return result.get("rows", [])
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []

def get_metadata_for_server(server_id: str) -> Dict[str, Any]:
    sql = """
    SELECT server_id, name, description, url, registry_source, metadata
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    try:
        result = ws_query(sql, params=(server_id,))
        rows = result.get("rows", [])
        if rows:
            return rows[0]
        return {}
    except Exception as e:
        log.error(f"Metadata query failed for {server_id}: {e}")
        return {}

def write_enrichment(server_id: str, score: float, confidence: float, evidence: Dict[str, Any]):
    computed_at = datetime.now(timezone.utc).isoformat()
    enrichment_id = compute_deterministic_id(server_id, "tool_description_safety")
    sql = """
    INSERT INTO mcp_signal_enrichments 
        (enrichment_id, server_id, signal_type, score, confidence, evidence_blob, computed_at)
    VALUES
        (?, ?, 'tool_description_safety', ?, ?, ?, ?)
    ON CONFLICT (server_id, signal_type) DO UPDATE SET
        enrichment_id = EXCLUDED.enrichment_id,
        score = EXCLUDED.score,
        confidence = EXCLUDED.confidence,
        evidence_blob = EXCLUDED.evidence_blob,
        computed_at = EXCLUDED.computed_at
    """
    params = (enrichment_id, server_id, score, confidence, json.dumps(evidence), computed_at)
    try:
        ws_execute(sql, params=params)
        log.info(f"Wrote enrichment for {server_id}: score={score:.3f}, confidence={confidence:.3f}")
    except Exception as e:
        log.error(f"Failed to write enrichment for {server_id}: {e}")

def cycle():
    log.info("Starting enrichment cycle")
    servers = get_unscored_servers()
    if not servers:
        log.info("No unscored servers found")
        return
    log.info(f"Processing {len(servers)} servers")
    processed = 0
    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue
        metadata = get_metadata_for_server(server_id)
        if not metadata:
            log.warning(f"No metadata for server {server_id}")
            continue
        result = compute_score(metadata)
        score = result.get("score", 0.0)
        confidence = result.get("confidence", 0.0)
        evidence = result.get("evidence", {})
        write_enrichment(server_id, score, confidence, evidence)
        processed += 1
    log.info(f"Cycle complete: processed {processed} servers")

def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"{SERVICE_NAME} starting")
    send_heartbeat()
    while True:
        try:
            cycle()
            send_heartbeat()
        except Exception as e:
            log.error(f"Cycle error: {e}")
            send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == "__main__":
    run()