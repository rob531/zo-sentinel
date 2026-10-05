import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

# HOUSE CONVENTIONS
SERVICE_NAME = 'signal_analyser_v2_wiring_check'
SERVICE_PORT = 8773
WRITE_SERVICE_URL = f'http://localhost:{SERVICE_PORT}/write'
PID_FILE = f'/home/workspace/logs/{SERVICE_NAME}.pid'
POLL_SECS = 300

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log')]
)
log = logging.getLogger(SERVICE_NAME)

# Enrichment signal types to track
ENRICHMENT_TYPES = [
    'permission_scope',
    'temporal_stability',
    'tool_description_safety',
    'supply_chain_enrichment',
    'community_signal_enrichment'
]


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f'/proc/{old_pid}'):
            log.error(f"Another instance running with PID {old_pid}. Exiting.")
            sys.exit(1)
        else:
            os.remove(PID_FILE)


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame: Any) -> None:
    log.info(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def ws_write(table: str, rows: Dict) -> None:
    payload = {
        'table': table,
        'rows': rows,
        'wait': True
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error(f"write_service failed for table {table}: {e}")


def ws_query(sql: str) -> List[Dict]:
    payload = {
        'sql': sql,
        'wait': True
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL.replace('/write', '/query'), json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except requests.RequestException as e:
        log.error(f"ws_query failed: {e}")
        return []


def send_heartbeat(status: str = 'running', meta: Optional[Dict] = None) -> None:
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': meta or {}
    }
    ws_write('service_health', row)


def get_enrichment_counts() -> Dict[str, int]:
    counts = {}
    for enrich_type in ENRICHMENT_TYPES:
        sql = f"""
        SELECT COUNT(*) as cnt FROM mcp_signal_enrichments
        WHERE signal_type = '{enrich_type}'
        """
        rows = ws_query(sql)
        counts[enrich_type] = rows[0]['cnt'] if rows else 0
    return counts


def get_total_servers() -> int:
    sql = "SELECT COUNT(*) as cnt FROM mcp_server_registry"
    rows = ws_query(sql)
    return rows[0]['cnt'] if rows else 0


def get_enriched_servers_count() -> int:
    sql = """
    SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_signal_enrichments
    WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety', 'supply_chain_enrichment', 'community_signal_enrichment')
    """
    rows = ws_query(sql)
    return rows[0]['cnt'] if rows else 0


def get_servers_with_all_enrichments() -> int:
    total = get_total_servers()
    if total == 0:
        return 0
    
    enrichment_types_str = "', '".join(ENRICHMENT_TYPES)
    sql = f"""
    SELECT COUNT(*) as cnt FROM (
        SELECT server_id, COUNT(DISTINCT signal_type) as type_count
        FROM mcp_signal_enrichments
        WHERE signal_type IN ('{enrichment_types_str}')
        GROUP BY server_id
        HAVING COUNT(DISTINCT signal_type) = {len(ENRICHMENT_TYPES)}
    )
    """
    rows = ws_query(sql)
    return rows[0]['cnt'] if rows else 0


def get_analyser_status() -> Dict[str, Any]:
    sql = """
    SELECT COUNT(*) as cnt FROM mcp_signal_scores
    WHERE computed_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
    """
    rows = ws_query(sql)
    return {'scores_computed_24h': rows[0]['cnt'] if rows else 0}


def run_wiring_diagnostics() -> Dict[str, Any]:
    diagnostics = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'enrichment_counts': {},
        'total_servers': 0,
        'enriched_servers': 0,
        'fully_enriched_servers': 0,
        'coverage_pct': 0.0,
        'analyser_status': {},
        'warnings': []
    }
    
    try:
        enrichment_counts = get_enrichment_counts()
        diagnostics['enrichment_counts'] = enrichment_counts
        
        total_servers = get_total_servers()
        diagnostics['total_servers'] = total_servers
        
        enriched_servers = get_enriched_servers_count()
        diagnostics['enriched_servers'] = enriched_servers
        
        fully_enriched = get_servers_with_all_enrichments()
        diagnostics['fully_enriched_servers'] = fully_enriched
        
        coverage_pct = (enriched_servers / total_servers * 100) if total_servers > 0 else 0.0
        diagnostics['coverage_pct'] = round(coverage_pct, 2)
        
        analyser_status = get_analyser_status()
        diagnostics['analyser_status'] = analyser_status
        
        missing_enrichments = [k for k, v in enrichment_counts.items() if v == 0]
        if missing_enrichments:
            diagnostics['warnings'].append(f"Missing enrichment data for: {', '.join(missing_enrichments)}")
        
        if total_servers > 0 and coverage_pct < 50:
            diagnostics['warnings'].append(f"Low enrichment coverage: {coverage_pct}%")
        
        if analyser_status.get('scores_computed_24h', 0) == 0:
            diagnostics['warnings'].append("No signal scores computed in last 24 hours")
            
    except Exception as e:
        log.error(f"Error during wiring diagnostics: {e}")
        diagnostics['warnings'].append(f"Diagnostic error: {str(e)}")
    
    return diagnostics


def log_diagnostics_report(diagnostics: Dict[str, Any]) -> None:
    log.info("=" * 60)
    log.info("SIGNAL ANALYSER V2 WIRING CHECK REPORT")
    log.info("=" * 60)
    log.info(f"Timestamp: {diagnostics['ts']}")
    log.info(f"Total MCP Servers: {diagnostics['total_servers']}")
    log.info(f"Servers with any enrichment: {diagnostics['enriched_servers']}")
    log.info(f"Servers with ALL enrichments: {diagnostics['fully_enriched_servers']}")
    log.info(f"Enrichment coverage: {diagnostics['coverage_pct']}%")
    log.info("")
    log.info("Enrichment counts by type:")
    for enrich_type, count in diagnostics['enrichment_counts'].items():
        log.info(f"  {enrich_type}: {count}")
    log.info("")
    log.info(f"Analyser status (24h): {diagnostics['analyser_status']}")
    if diagnostics['warnings']:
        log.warning("WARNINGS:")
        for warning in diagnostics['warnings']:
            log.warning(f"  - {warning}")
    else:
        log.info("No warnings detected.")
    log.info("=" * 60)


def write_diagnostics_to_table(diagnostics: Dict[str, Any]) -> None:
    ws_write('wiring_check_results', {
        'check_service': SERVICE_NAME,
        'ts': diagnostics['ts'],
        'total_servers': diagnostics['total_servers'],
        'enriched_servers': diagnostics['enriched_servers'],
        'fully_enriched_servers': diagnostics['fully_enriched_servers'],
        'coverage_pct': diagnostics['coverage_pct'],
        'enrichment_counts_json': str(diagnostics['enrichment_counts']),
        'analyser_status_json': str(diagnostics['analyser_status']),
        'warnings_json': str(diagnostics['warnings'])
    })


def cycle() -> None:
    log.info("Starting signal_analyser_v2 wiring diagnostic cycle...")
    diagnostics = run_wiring_diagnostics()
    log_diagnostics_report(diagnostics)
    write_diagnostics_to_table(diagnostics)
    send_heartbeat(status='running', meta={'coverage_pct': diagnostics['coverage_pct']})


def run() -> None:
    check_single_instance()
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"{SERVICE_NAME} starting. PID={os.getpid()}, POLL_SECS={POLL_SECS}")
    
    try:
        while True:
            cycle()
            time.sleep(POLL_SECS)
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received.")
    finally:
        remove_pid_file()


if __name__ == '__main__':
    run()