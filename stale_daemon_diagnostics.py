import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests

SERVICE_NAME = 'stale_daemon_diagnostics'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
TIMEOUT_SECONDS = 10

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)

STALE_THRESHOLD_HOURS = 2
STALE_THRESHOLD_SECONDS = STALE_THRESHOLD_HOURS * 3600

TRACKED_DAEMONS = {
    'write_service',
    'self_diagnostics',
    'rug_pull_monitor',
    'wisdom_synthesiser',
    'threat_intel_ingestor',
    'sentinel_directive_generator',
    'mesh_memory',
    'probe_consumer',
    'inference_router',
    'zo_mcp_server',
}


def ws_query(sql: str, params: Optional[tuple] = None) -> list:
    payload = {'table': '__direct_sql__', 'sql': sql, 'params': params or ()}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table: str, rows: list) -> dict:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def get_stale_services() -> list:
    sql = """
    SELECT service_name, last_heartbeat, status, meta
    FROM service_health
    WHERE last_heartbeat IS NOT NULL
    ORDER BY last_heartbeat ASC
    """
    rows = ws_query(sql)
    return rows


def parse_heartbeat_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        if ts_str.endswith('Z'):
            ts_str = ts_str[:-1] + '+00:00'
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def check_daemon_connectivity(service_name: str) -> dict:
    connectivity_result = {
        'service': service_name,
        'reachable': None,
        'error': None,
        'probe_method': None
    }
    
    if service_name == 'write_service':
        try:
            resp = requests.post(
                WRITE_SERVICE_URL,
                json={'table': '__health__', 'rows': [], 'wait': False},
                timeout=5
            )
            connectivity_result['reachable'] = resp.status_code == 200
            connectivity_result['probe_method'] = 'write_service_health_check'
        except requests.exceptions.ConnectionError as e:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = 'connection_refused'
        except requests.exceptions.Timeout:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = 'timeout'
        except Exception as e:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = str(type(e).__name__)
    elif service_name == 'zo_mcp_server':
        try:
            resp = requests.get('http://127.0.0.1:8774/health', timeout=5)
            connectivity_result['reachable'] = resp.status_code == 200
            connectivity_result['probe_method'] = 'mcp_health_endpoint'
        except requests.exceptions.ConnectionError:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = 'connection_refused'
        except requests.exceptions.Timeout:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = 'timeout'
        except Exception as e:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = str(type(e).__name__)
    elif service_name == 'inference_router':
        try:
            resp = requests.get('http://127.0.0.1:8773/health', timeout=5)
            connectivity_result['reachable'] = resp.status_code == 200
            connectivity_result['probe_method'] = 'inference_health_endpoint'
        except requests.exceptions.ConnectionError:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = 'connection_refused'
        except requests.exceptions.Timeout:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = 'timeout'
        except Exception as e:
            connectivity_result['reachable'] = False
            connectivity_result['error'] = str(type(e).__name__)
    else:
        connectivity_result['probe_method'] = 'no_probe_configured'
        connectivity_result['error'] = 'connectivity_check_not_implemented'
    
    return connectivity_result


def diagnose_stale_daemon(service_name: str, last_heartbeat: str, status: str, meta: str) -> dict:
    now = datetime.now(timezone.utc)
    heartbeat_dt = parse_heartbeat_ts(last_heartbeat)
    
    if heartbeat_dt is None:
        return {
            'daemon': service_name,
            'age_seconds': None,
            'likely_cause': 'unparseable_heartbeat_timestamp',
            'suggested_action': 'inspect_service_health_table_row',
            'status': status,
            'meta': meta
        }
    
    if heartbeat_dt.tzinfo is None:
        heartbeat_dt = heartbeat_dt.replace(tzinfo=timezone.utc)
    
    age_seconds = int((now - heartbeat_dt).total_seconds())
    age_hours = age_seconds / 3600
    
    connectivity = check_daemon_connectivity(service_name)
    
    if age_seconds < STALE_THRESHOLD_SECONDS:
        return {
            'daemon': service_name,
            'age_seconds': age_seconds,
            'likely_cause': 'not_stale',
            'suggested_action': 'no_action_required',
            'status': status,
            'meta': meta
        }
    
    if connectivity.get('reachable') is False:
        if connectivity.get('error') == 'connection_refused':
            likely_cause = 'service_not_running_connection_refused'
            suggested_action = 'investigate_process_termination_check_pid_file'
        elif connectivity.get('error') == 'timeout':
            likely_cause = 'service_host_unreachable_or_hung'
            suggested_action = 'check_service_process_and_network_connectivity'
        else:
            likely_cause = f"service_unreachable_{connectivity.get('error')}"
            suggested_action = 'check_service_process_and_logs'
    elif connectivity.get('reachable') is True:
        likely_cause = 'heartbeat_stuck_service_but_alive'
        suggested_action = 'check_write_service_ingestion_lag_or_heartbeat_loop_bug'
        logger.warning(f"[{service_name}] ALIVE but heartbeat stuck ({age_hours:.1f}h old)")
    elif connectivity.get('error') == 'connectivity_check_not_implemented':
        likely_cause = 'heartbeat_expired_no_connectivity_probe'
        suggested_action = 'add_connectivity_check_for_daemon_type'
    else:
        likely_cause = 'unknown_heartbeat_staleness'
        suggested_action = 'manual_investigation_required'
    
    diagnostic = {
        'daemon': service_name,
        'age_seconds': age_seconds,
        'age_hours': round(age_hours, 2),
        'likely_cause': likely_cause,
        'suggested_action': suggested_action,
        'connectivity': connectivity,
        'status': status,
        'meta': meta,
        'threshold_hours': STALE_THRESHOLD_HOURS,
        'last_heartbeat': last_heartbeat,
        'checked_at': datetime.utcnow().isoformat() + 'Z'
    }
    
    return diagnostic


def run_diagnostics() -> list:
    logger.info("Starting stale daemon diagnostics cycle")
    
    services = get_stale_services()
    logger.info(f"Retrieved {len(services)} service health records")
    
    stale_records = []
    all_diagnostics = []
    
    for row in services:
        service_name = row.get('service_name', '')
        last_heartbeat = row.get('last_heartbeat', '')
        status = row.get('status', 'unknown')
        meta = row.get('meta', '{}')
        
        diagnostic = diagnose_stale_daemon(service_name, last_heartbeat, status, meta)
        all_diagnostics.append(diagnostic)
        
        heartbeat_dt = parse_heartbeat_ts(last_heartbeat)
        if heartbeat_dt:
            if heartbeat_dt.tzinfo is None:
                heartbeat_dt = heartbeat_dt.replace(tzinfo=timezone.utc)
            age_seconds = int((datetime.now(timezone.utc) - heartbeat_dt).total_seconds())
            
            if age_seconds > STALE_THRESHOLD_SECONDS:
                stale_records.append(diagnostic)
                logger.warning(
                    f"STALE DAEMON: {service_name} | age={age_seconds}s ({age_seconds/3600:.1f}h) | "
                    f"cause={diagnostic['likely_cause']} | action={diagnostic['suggested_action']}"
                )
    
    logger.info(
        f"Diagnostics complete: {len(stale_records)} stale / {len(all_diagnostics)} total"
    )
    
    return {
        'diagnostics': all_diagnostics,
        'stale_count': len(stale_records),
        'stale_daemons': stale_records,
        'checked_at': datetime.utcnow().isoformat() + 'Z',
        'threshold_hours': STALE_THRESHOLD_HOURS
    }


def emit_diagnostics_report() -> dict:
    result = run_diagnostics()
    
    diagnostics_list = result['diagnostics']
    stale_list = result['stale_daemons']
    
    report = {
        'report_type': 'stale_daemon_diagnostics',
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'stale_threshold_hours': STALE_THRESHOLD_HOURS,
        'total_services_checked': len(diagnostics_list),
        'stale_count': len(stale_list),
        'stale_daemons': stale_list,
        'healthy_daemons': [
            d for d in diagnostics_list 
            if d.get('age_seconds', 999999) <= STALE_THRESHOLD_SECONDS
        ]
    }
    
    ws_write('stale_diagnostics_log', [report])
    
    return result


if __name__ == '__main__':
    result = emit_diagnostics_report()
    logger.info(f"Stale daemon diagnostics complete: {result['stale_count']} stale daemons found")
    
    for stale in result['stale_daemons']:
        logger.info(
            f"STALE: {stale['daemon']} | age={stale['age_seconds']}s | "
            f"cause={stale['likely_cause']} | action={stale['suggested_action']}"
        )
    
    sys.exit(0 if result['stale_count'] == 0 else 1)