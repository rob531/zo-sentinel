import os
import json
import logging
import threading
import time
from datetime import datetime
from typing import Dict, Any, Set, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import requests
import uvicorn

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

SERVICE_NAME = "live_threat_cross_referencer"
PORT = 8793
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772"
CYCLE_INTERVAL = 1800
LOOKBACK_HOURS = 24

processed_event_ids: Set[str] = set()
processed_lock = threading.Lock()

def get_write_url() -> str:
    return WRITE_SERVICE_URL

def get_query_url() -> str:
    return QUERY_URL

def get_execute_url() -> str:
    return EXECUTE_URL

def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        response = requests.post(get_query_url(), json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result.get("data", [])
    except Exception as e:
        logger.error(f"ws_query error: {e}")
        return []

def ws_write(table: str, row: Dict[str, Any]) -> bool:
    payload = {"table": table, "rows": row}
    try:
        response = requests.post(get_write_url(), json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write error: {e}")
        return False

def ws_execute(sql: str) -> bool:
    payload = {"sql": sql}
    try:
        response = requests.post(get_execute_url(), json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute error: {e}")
        return False

def send_heartbeat() -> None:
    heartbeat_data = {
        "table": "service_health",
        "rows": {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        }
    }
    try:
        requests.post(get_write_url(), json=heartbeat_data, timeout=10)
    except Exception as e:
        logger.error(f"Heartbeat failed: {e}")

def load_processed_events() -> Set[str]:
    filepath = f"/tmp/{SERVICE_NAME}_processed.txt"
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return set(line.strip() for line in f if line.strip())
    except Exception as e:
        logger.error(f"Error loading processed events: {e}")
    return set()

def save_processed_events(event_ids: Set[str]) -> None:
    filepath = f"/tmp/{SERVICE_NAME}_processed.txt"
    try:
        with open(filepath, 'w') as f:
            for event_id in sorted(event_ids):
                f.write(f"{event_id}\n")
    except Exception as e:
        logger.error(f"Error saving processed events: {e}")

def fetch_threat_events() -> List[Dict[str, Any]]:
    sql = f"""
    SELECT event_id, event_type, payload, source, severity, created_at
    FROM mesh_events
    WHERE event_type IN ('threat_feed_match', 'cisa_kev', 'urlhaus_hit', 'malware_bazaar')
    AND created_at > now() - INTERVAL '{LOOKBACK_HOURS} HOURS'
    ORDER BY created_at DESC
    """
    results = ws_query(sql)
    logger.info(f"Fetched {len(results)} threat events from last {LOOKBACK_HOURS} hours")
    return results

def extract_indicators(event: Dict[str, Any]) -> List[Dict[str, str]]:
    indicators = []
    try:
        payload = event.get('payload', {})
        if isinstance(payload, str):
            payload = json.loads(payload)
        indicator_fields = ['ip', 'domain', 'package_name', 'url', 'hash']
        for field in indicator_fields:
            if field in payload:
                value = str(payload[field]).strip()
                if value:
                    indicators.append({
                        'type': field,
                        'value': value,
                        'source': event.get('source', event.get('event_type', 'unknown')),
                        'event_id': str(event.get('event_id', '')),
                        'event_type': event.get('event_type', 'unknown')
                    })
    except Exception as e:
        logger.warning(f"Failed to extract indicators: {e}")
    return indicators

def find_matching_servers(indicators: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not indicators:
        return []
    conditions = []
    for ind in indicators:
        value = ind['value'].replace("'", "''")
        if ind['type'] == 'domain':
            conditions.append(f"(url ILIKE '%{value}%' OR metadata ILIKE '%{value}%' OR name ILIKE '%{value}%')")
        elif ind['type'] == 'package_name':
            conditions.append(f"(url ILIKE '%{value}%' OR metadata ILIKE '%{value}%' OR name ILIKE '%{value}%')")
        elif ind['type'] == 'ip':
            conditions.append(f"url ILIKE '%{value}%'")
        elif ind['type'] == 'url':
            conditions.append(f"(url ILIKE '%{value}%' OR metadata ILIKE '%{value}%')")
        elif ind['type'] == 'hash':
            conditions.append(f"metadata ILIKE '%{value}%'")
    if not conditions:
        return []
    query = f"""
    SELECT server_id, name, url, metadata, trust_score, verdict
    FROM mcp_server_registry
    WHERE {' OR '.join(conditions)}
    """
    matches = ws_query(query)
    logger.info(f"Found {len(matches)} matching servers for {len(indicators)} indicators")
    return matches

def record_threat_association(server: Dict[str, Any], indicator: Dict[str, str]) -> None:
    evidence = {
        'indicator': indicator['value'],
        'indicator_type': indicator['type'],
        'threat_event_id': indicator['event_id'],
        'threat_event_type': indicator['event_type'],
        'source': indicator['source']
    }
    association = {
        'server_id': server['server_id'],
        'threat_type': 'threat_feed_cross_ref',
        'evidence': json.dumps(evidence),
        'severity': 'HIGH',
        'reported_at': datetime.utcnow().isoformat()
    }
    ws_write('mcp_threat_associations', association)
    logger.info(f"Recorded threat association for server {server['server_id']}")

def update_server_risk(server: Dict[str, Any]) -> None:
    server_id = server['server_id']
    sql = f"""
    UPDATE mcp_server_registry
    SET trust_score = CASE WHEN trust_score IS NULL THEN 0 ELSE GREATEST(0, trust_score - 25) END,
        verdict = 'HIGH_RISK_ISOLATED',
        last_assessed = now()
    WHERE server_id = '{server_id}'
    """
    ws_execute(sql)
    logger.info(f"Updated risk for server {server_id}")

def log_cross_ref_event(server: Dict[str, Any], indicator: Dict[str, str]) -> None:
    payload = {
        'mcp_name': server.get('name', 'unknown'),
        'mcp_server_id': server['server_id'],
        'indicator': indicator['value'],
        'indicator_type': indicator['type'],
        'threat_source': indicator['source'],
        'threat_event_type': indicator['event_type'],
        'threat_event_id': indicator['event_id']
    }
    event = {
        'event_type': 'cross_ref_hit',
        'severity': 'CRITICAL',
        'payload': json.dumps(payload),
        'source': SERVICE_NAME,
        'created_at': datetime.utcnow().isoformat()
    }
    ws_write('mesh_events', event)
    logger.warning(f"CROSS-REF HIT: Server '{server.get('name')}' matched indicator '{indicator['value']}' from {indicator['source']}")

def process_threat_events() -> None:
    global processed_event_ids
    logger.info("Starting threat cross-reference cycle")
    processed_event_ids = load_processed_events()
    logger.info(f"Loaded {len(processed_event_ids)} previously processed events")
    threat_events = fetch_threat_events()
    new_processed = set()
    cross_ref_count = 0
    for event in threat_events:
        event_id = str(event.get('event_id', ''))
        if not event_id:
            continue
        if event_id in processed_event_ids:
            continue
        indicators = extract_indicators(event)
        if not indicators:
            new_processed.add(event_id)
            continue
        matching_servers = find_matching_servers(indicators)
        for server in matching_servers:
            for indicator in indicators:
                record_threat_association(server, indicator)
                update_server_risk(server)
                log_cross_ref_event(server, indicator)
                cross_ref_count += 1
        new_processed.add(event_id)
    processed_event_ids.update(new_processed)
    if len(processed_event_ids) > 10000:
        processed_event_ids = set(sorted(processed_event_ids)[-10000:])
    save_processed_events(processed_event_ids)
    logger.info(f"Cycle complete. New events: {len(new_processed)}, Cross-refs: {cross_ref_count}, Total tracked: {len(processed_event_ids)}")

def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(60)

def cycle_loop() -> None:
    while True:
        process_threat_events()
        time.sleep(CYCLE_INTERVAL)

def run_cycle() -> None:
    try:
        process_threat_events()
    except Exception as e:
        logger.error(f"Error in cycle: {e}")

app = FastAPI(title=SERVICE_NAME)

@app.get("/health")
async def health():
    return {"status": "healthy", "service": SERVICE_NAME, "port": PORT}

@app.get("/stats")
async def stats():
    return {
        "service": SERVICE_NAME,
        "processed_events": len(processed_event_ids),
        "cycle_interval_seconds": CYCLE_INTERVAL,
        "lookback_hours": LOOKBACK_HOURS
    }

def run() -> None:
    logger.info(f"Starting {SERVICE_NAME} on port {PORT}")
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    cycle_thread = threading.Thread(target=cycle_loop, daemon=True)
    cycle_thread.start()
    run_cycle()
    uvicorn.run(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    run()