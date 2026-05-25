import os
import sys
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'write_service_heartbeat_investigator'
SERVICE_PORT = None
PID_FILE = None

CRITICAL_THRESHOLD_HOURS = 2


def ws_query(sql: str, params: Optional[tuple] = None) -> list:
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json=payload,
        timeout=10
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get('success'):
        return result.get('rows', [])
    raise Exception(f"Query failed: {result}")


def ws_write(table: str, rows: list) -> dict:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    if not result.get('success'):
        raise Exception(f"Write failed: {result}")
    return result


def check_write_service_health() -> dict:
    try:
        resp = requests.get(
            f'{WRITE_SERVICE_URL}/health',
            timeout=10
        )
        if resp.status_code == 200:
            return {'reachable': True, 'healthy': True, 'status_code': 200}
        return {'reachable': True, 'healthy': False, 'status_code': resp.status_code}
    except requests.exceptions.ConnectionError:
        return {'reachable': False, 'healthy': False, 'error': 'connection_refused'}
    except requests.exceptions.Timeout:
        return {'reachable': False, 'healthy': False, 'error': 'timeout'}
    except Exception as e:
        return {'reachable': False, 'healthy': False, 'error': str(e)}


def get_write_service_heartbeat_history() -> list:
    sql = """
    SELECT 
        last_heartbeat,
        status,
        ts
    FROM service_health
    WHERE service_name = 'write_service'
    ORDER BY ts DESC
    LIMIT 20
    """
    return ws_query(sql)


def compute_missed_beats(last_heartbeat_iso: str) -> int:
    last_dt = datetime.fromisoformat(last_heartbeat_iso.replace('Z', '+00:00'))
    now_dt = datetime.now(timezone.utc)
    delta = now_dt - last_dt
    total_minutes = delta.total_seconds() / 60
    missed = int(total_minutes // 5)
    return missed


def determine_likely_cause(
    reachable: bool,
    missed_beats: int,
    last_status: Optional[str]
) -> str:
    if not reachable:
        return 'crashed'
    if reachable and missed_beats > 10:
        return 'hanging'
    if reachable and missed_beats > 2:
        return 'slow_cycle'
    if last_status in ('error', 'failed'):
        return 'unhealthy_status'
    return 'unknown'


def generate_report_id(last_heartbeat_iso: str, missed_beats: int, service_responsive: bool) -> str:
    content = f"{last_heartbeat_iso}:{missed_beats}:{service_responsive}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def investigate() -> dict:
    logger.info("Starting write_service heartbeat investigation")
    
    health_check = check_write_service_health()
    logger.info(f"Health check result: {health_check}")
    
    history = get_write_service_heartbeat_history()
    
    if not history:
        logger.warning("No heartbeat history found for write_service")
        report = {
            'investigator': SERVICE_NAME,
            'ts': datetime.now(timezone.utc).isoformat(),
            'last_heartbeat': None,
            'consecutive_missed_beats': None,
            'service_responsive': health_check.get('reachable', False),
            'likely_cause': 'no_history_found',
            'health_check': health_check,
            'severity': 'warning'
        }
        return report
    
    latest = history[0]
    last_heartbeat = latest.get('last_heartbeat')
    last_status = latest.get('status')
    
    if last_heartbeat:
        missed_beats = compute_missed_beats(last_heartbeat)
    else:
        missed_beats = 0
    
    service_responsive = health_check.get('reachable', False)
    likely_cause = determine_likely_cause(service_responsive, missed_beats, last_status)
    
    if missed_beats > 24:
        severity = 'critical'
    elif missed_beats > 6:
        severity = 'high'
    elif missed_beats > 2:
        severity = 'medium'
    else:
        severity = 'info'
    
    report = {
        'investigator': SERVICE_NAME,
        'ts': datetime.now(timezone.utc).isoformat(),
        'last_heartbeat': last_heartbeat,
        'consecutive_missed_beats': missed_beats,
        'service_responsive': service_responsive,
        'likely_cause': likely_cause,
        'health_check': health_check,
        'last_status': last_status,
        'history_count': len(history),
        'severity': severity
    }
    
    logger.info(f"Investigation complete: {report}")
    return report


def main():
    report = investigate()
    
    ws_write('sentinel_heartbeat_investigations', [{
        'investigation_id': generate_report_id(
            report.get('last_heartbeat', 'none'),
            report.get('consecutive_missed_beats', 0),
            report.get('service_responsive', False)
        ),
        'investigator': SERVICE_NAME,
        'ts': datetime.now(timezone.utc).isoformat(),
        'target_service': 'write_service',
        'last_heartbeat': report.get('last_heartbeat'),
        'consecutive_missed_beats': report.get('consecutive_missed_beats'),
        'service_responsive': report.get('service_responsive'),
        'likely_cause': report.get('likely_cause'),
        'severity': report.get('severity'),
        'health_check_reachable': report.get('health_check', {}).get('reachable'),
        'health_check_healthy': report.get('health_check', {}).get('healthy'),
        'last_status': report.get('last_status'),
        'history_count': report.get('history_count')
    }])
    
    print(f"INVESTIGATION REPORT: {report}")
    sys.exit(0)


if __name__ == '__main__':
    main()