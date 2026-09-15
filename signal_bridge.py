import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

LOG_DIR = os.environ.get('ZO_SENTINEL_LOGS', '/home/workspace/zo_sentinel/logs')
LOG_FILE = os.path.join(LOG_DIR, 'signal_bridge.log')
PID_FILE = '/tmp/signal_bridge.pid'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('signal_bridge')

WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE', 'http://127.0.0.1:8772')
QUERY_SERVICE_URL = os.environ.get('QUERY_SERVICE', 'http://127.0.0.1:8772')

ENRICHMENT_TO_SIGNAL = {
    'supply_chain_enrichment': 'supply_chain_score',
    'domain_trust_enrichment': 'domain_trust_score',
    'community_signal_enrichment': 'community_signal_score',
    'temporal_stability_enrichment': 'temporal_stability_score',
    'permission_scope_enrichment': 'permission_scope_score',
    'tool_description_safety_enrichment': 'tool_description_safety_score',
    'context_efficiency_enrichment': 'context_efficiency_score',
    'evidence_density_enrichment': 'evidence_density_score',
    'registry_breadth_enrichment': 'registry_breadth_score',
    'injection_resilience_enrichment': 'injection_resilience_score',
    'vendor_concentration_enrichment': 'vendor_concentration_score',
    'mcp_traffic_fingerprints': 'protocol_confirmation',
}

DISCRIMINATION_FLOOR = 0.05
STALE_THRESHOLD_SECONDS = 300


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    try:
        import requests
        resp = requests.post(f'{QUERY_SERVICE_URL}/query', json={'sql': sql}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        else:
            log.warning(f"Query failed [{resp.status_code}]: {sql[:100]}")
            return None
    except Exception as e:
        log.error(f"Query error: {e}")
        return None


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        import requests
        resp = requests.post(f'{WRITE_SERVICE_URL}/write', json={'table': table, 'rows': rows}, timeout=30)
        if resp.status_code == 200:
            return True
        else:
            log.warning(f"Write failed [{resp.status_code}]: {resp.text[:200]}")
            return False
    except Exception as e:
        log.error(f"Write error: {e}")
        return False


def get_distinct_score_count(signal_name: str) -> int:
    result = ws_query(f"""
        SELECT COUNT(DISTINCT score) as cnt 
        FROM mcp_signal_scores 
        WHERE signal_name = '{signal_name}'
    """)
    if result and len(result) > 0:
        return result[0].get('cnt', 0)
    return 0


def write_discrimination_floor_breach(signal_name: str, server_id: str, score: float, computed_at: str):
    msg = f"FLOOR_BREACH signal={signal_name} server_id={server_id} score={score} computed_at={computed_at}"
    log.warning(msg)
    ws_write('audit_log', [{
        'event_type': 'discrimination_floor_breach',
        'actor': 'signal_bridge',
        'detail': msg,
        'target_server_id': server_id[:100] if server_id else 'unknown'
    }])


def get_unbridged_enrichments() -> List[Dict[str, Any]]:
    excluded = ', '.join([f"'{k}'" for k in ENRICHMENT_TO_SIGNAL.keys()])
    result = ws_query(f"""
        SELECT e.server_id, e.signal_type, e.score, e.computed_at, e.evidence_blob AS evidence
        FROM mcp_signal_enrichments e
        WHERE e.signal_type IN ({excluded})
          AND e.server_id NOT LIKE '__harness_%'
          AND e.server_id IN (SELECT server_id FROM mcp_server_registry)
          AND NOT EXISTS (
              SELECT 1 FROM mcp_signal_scores s
              WHERE s.server_id = e.server_id
                AND s.signal_name = CASE e.signal_type
                    WHEN 'mcp_traffic_fingerprints' THEN 'protocol_confirmation_score'
                    WHEN 'supply_chain_enrichment' THEN 'supply_chain_score'
                    WHEN 'domain_trust_enrichment' THEN 'domain_trust_score'
                    WHEN 'community_signal_enrichment' THEN 'community_signal_score'
                    WHEN 'temporal_stability_enrichment' THEN 'temporal_stability_score'
                    WHEN 'permission_scope_enrichment' THEN 'permission_scope_score'
                    WHEN 'tool_description_safety_enrichment' THEN 'tool_description_safety_score'
                    WHEN 'context_efficiency_enrichment' THEN 'context_efficiency_score'
                    WHEN 'evidence_density_enrichment' THEN 'evidence_density_score'
                    WHEN 'registry_breadth_enrichment' THEN 'registry_breadth_score'
                    WHEN 'injection_resilience_enrichment' THEN 'injection_resilience_score'
                    WHEN 'vendor_concentration_enrichment' THEN 'vendor_concentration_score'
                END
          )
        LIMIT 500
    """)
    return result if result else []


def write_scores(entries: List[Dict[str, Any]]) -> int:
    if not entries:
        return 0
    rows = []
    for entry in entries:
        signal_name = ENRICHMENT_TO_SIGNAL.get(entry.get('signal_type', ''))
        if not signal_name:
            continue
        score = entry.get('score')
        if score is None:
            continue
        server_id = entry.get('server_id', '')
        computed_at = entry.get('computed_at', utc_now_iso())
        evidence = entry.get('evidence', '{}')
        rows.append({
            'server_id': server_id,
            'signal_name': signal_name,
            'score': float(score),
            'evidence': evidence,
            'scored_at': computed_at
        })
        if float(score) < DISCRIMINATION_FLOOR:
            write_discrimination_floor_breach(signal_name, server_id, score, computed_at)
    if rows:
        if ws_write('mcp_signal_scores', rows):
            return len(rows)
        else:
            log.error(f"Failed to write {len(rows)} scores")
            return 0
    return 0


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Already running as PID {old_pid}")
                return False
            except OSError:
                pass
        except (ValueError, IOError):
            pass
    try:
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
    except IOError:
        pass
    return True


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    log.info("Received signal, shutting down...")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    ws_write('service_health', [{'service': 'signal_bridge', 'last_heartbeat': utc_now_iso()}])


def run():
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        log.error("Failed to acquire lock")
        sys.exit(1)
    
    log.info("signal_bridge started")
    send_heartbeat()
    
    cycle = 0
    while True:
        cycle += 1
        try:
            enrichments = get_unbridged_enrichments()
            count = len(enrichments)
            if count > 0:
                written = write_scores(enrichments)
                log.info(f"bridged {written}/{count} enrichments this cycle")
            else:
                log.info("no new enrichments to bridge")
            
            for sig_type in ENRICHMENT_TO_SIGNAL.keys():
                dist = get_distinct_score_count(ENRICHMENT_TO_SIGNAL[sig_type])
                log.info(f"signal={ENRICHMENT_TO_SIGNAL[sig_type]} distinct_scores={dist}")
            
        except Exception as e:
            log.error(f"Cycle error: {e}")
        
        send_heartbeat()
        time.sleep(60)


if __name__ == '__main__':
    run()