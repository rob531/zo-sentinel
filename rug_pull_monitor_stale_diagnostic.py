import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/rug_pull_monitor_stale_diagnostic.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'rug_pull_monitor_stale_diagnostic'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = f'{WRITE_SERVICE_URL}/query'
WRITE_URL = f'{WRITE_SERVICE_URL}/write'
PID_FILE = '/var/run/zo_sentinel/rug_pull_monitor.pid'


def ws_query(sql: str) -> list:
    """Execute a SELECT query via write_service."""
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.RequestException as e:
        logger.error(f'ws_query failed: {e}')
        return []


def ws_write(table: str, rows: list) -> bool:
    """Write rows via write_service (for heartbeat only)."""
    try:
        resp = requests.post(WRITE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=15)
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        logger.error(f'ws_write failed: {e}')
        return False


def get_last_heartbeat(service: str) -> dict | None:
    """Query service_health for the last heartbeat of a service."""
    sql = f"SELECT service, last_heartbeat FROM service_health WHERE service = '{service}'"
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return None


def parse_iso_to_utc(ts_str: str) -> datetime | None:
    """Parse an ISO 8601 timestamp string to datetime."""
    if not ts_str:
        return None
    try:
        if ts_str.endswith('Z'):
            ts_str = ts_str[:-1] + '+00:00'
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except ValueError:
        return None


def calculate_age_seconds(last_heartbeat_str: str) -> float | None:
    """Calculate age in seconds between last heartbeat and now."""
    last_ts = parse_iso_to_utc(last_heartbeat_str)
    if not last_ts:
        return None
    now = datetime.now(timezone.utc)
    return (now - last_ts).total_seconds()


def format_age(seconds: float) -> str:
    """Format seconds into human-readable age string."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f'{hours}h{minutes}m'


def check_pid_file(pid_file: str) -> dict:
    """Check if PID file exists and extract PID."""
    result = {'exists': False, 'pid': None, 'readable': False}
    if os.path.exists(pid_file):
        result['exists'] = True
        try:
            with open(pid_file, 'r') as f:
                pid_str = f.read().strip()
                result['pid'] = int(pid_str) if pid_str else None
                result['readable'] = True
        except (IOError, ValueError) as e:
            result['error'] = str(e)
    else:
        result['reason'] = 'PID file not found at expected path'
    return result


def check_process_state(pid: int) -> dict:
    """Check process state via /proc filesystem."""
    result = {'alive': False, 'state': None, 'cmdline': None, 'stat': None}
    proc_path = f'/proc/{pid}'
    try:
        if os.path.exists(proc_path):
            result['alive'] = True
            stat_path = os.path.join(proc_path, 'stat')
            cmdline_path = os.path.join(proc_path, 'cmdline')
            try:
                with open(stat_path, 'r') as f:
                    stat_content = f.read()
                    parts = stat_content.split(' ', 2)
                    result['stat'] = parts[1] if len(parts) > 1 else None
                    if len(parts) > 2:
                        state_start = stat_content.find('(')
                        state_end = stat_content.rfind(')')
                        if state_start != -1 and state_end != -1:
                            result['cmdline'] = stat_content[state_start+1:state_end]
            except IOError as e:
                result['stat_error'] = str(e)
            try:
                with open(cmdline_path, 'rb') as f:
                    cmdline_raw = f.read()
                    result['cmdline_raw'] = cmdline_raw.replace(b'\x00', b' ').strip().decode('utf-8', errors='replace')
            except IOError as e:
                result['cmdline_error'] = str(e)
        else:
            result['reason'] = f'Process {pid} not found in /proc'
    except PermissionError as e:
        result['permission_error'] = str(e)
    return result


def check_process_by_pgrep() -> list:
    """Check for rug_pull_monitor process via pgrep."""
    result = []
    try:
        import subprocess
        proc = subprocess.run(
            ['pgrep', '-f', 'rug_pull_monitor'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if proc.returncode == 0:
            pids = proc.stdout.strip().split('\n')
            for pid_str in pids:
                if pid_str:
                    result.append({'pid': int(pid_str), 'source': 'pgrep'})
        else:
            result = []
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        logger.warning(f'pgrep check failed: {e}')
    return result


def get_service_health_record() -> dict | None:
    """Get full service_health record for rug_pull_monitor."""
    sql = "SELECT * FROM service_health WHERE service = 'rug_pull_monitor'"
    rows = ws_query(sql)
    return rows[0] if rows else None


def assess_severity(age_seconds: float) -> str:
    """Assess severity based on heartbeat age."""
    hours = age_seconds / 3600
    if hours < 1:
        return 'OK'
    elif hours < 4:
        return 'WARNING'
    elif hours < 24:
        return 'ELEVATED'
    elif hours < 72:
        return 'HIGH'
    else:
        return 'CRITICAL'


def run_diagnostic() -> dict:
    """Run the complete diagnostic probe."""
    logger.info('Starting rug_pull_monitor stale heartbeat diagnostic')
    report = {
        'started_at': datetime.now(timezone.utc).isoformat(),
        'service': 'rug_pull_monitor',
        'findings': [],
        'summary': {}
    }

    health = get_service_health_record()
    if not health:
        report['findings'].append({
            'check': 'service_health',
            'status': 'FAIL',
            'detail': 'No service_health record found for rug_pull_monitor'
        })
        report['summary']['has_heartbeat_record'] = False
        return report

    report['summary']['has_heartbeat_record'] = True
    last_hb = health.get('last_heartbeat', 'unknown')
    report['last_heartbeat'] = last_hb

    age_seconds = calculate_age_seconds(last_hb)
    if age_seconds is not None:
        age_str = format_age(age_seconds)
        severity = assess_severity(age_seconds)
        report['findings'].append({
            'check': 'heartbeat_age',
            'status': severity,
            'detail': f'Heartbeat age: {age_str} ({age_seconds:.0f} seconds)',
            'age_seconds': age_seconds,
            'age_formatted': age_str
        })
        report['summary']['heartbeat_age_seconds'] = age_seconds
        report['summary']['severity'] = severity
    else:
        report['findings'].append({
            'check': 'heartbeat_age',
            'status': 'FAIL',
            'detail': f'Could not parse last_heartbeat value: {last_hb}'
        })

    pid_info = check_pid_file(PID_FILE)
    report['findings'].append({
        'check': 'pid_file',
        'status': 'PASS' if pid_info['exists'] else 'FAIL',
        'detail': pid_info
    })
    report['summary']['pid_file_exists'] = pid_info['exists']

    if pid_info['pid']:
        proc_state = check_process_state(pid_info['pid'])
        report['findings'].append({
            'check': 'process_state',
            'status': 'PASS' if proc_state['alive'] else 'FAIL',
            'detail': proc_state
        })
        report['summary']['process_alive'] = proc_state['alive']

    pgrep_result = check_process_by_pgrep()
    if pgrep_result:
        report['findings'].append({
            'check': 'pgrep_search',
            'status': 'PASS',
            'detail': f'Found {len(pgrep_result)} rug_pull_monitor processes',
            'processes': pgrep_result
        })
        report['summary']['pgrep_found'] = True
        report['summary']['pgrep_count'] = len(pgrep_result)
    else:
        report['findings'].append({
            'check': 'pgrep_search',
            'status': 'FAIL',
            'detail': 'No rug_pull_monitor processes found via pgrep'
        })
        report['summary']['pgrep_found'] = False

    all_checks = [f['status'] for f in report['findings']]
    if 'CRITICAL' in all_checks or 'FAIL' in all_checks:
        verdict = 'STALE - requires intervention'
    elif 'HIGH' in all_checks or 'ELEVATED' in all_checks:
        verdict = 'STALE - monitor closely'
    else:
        verdict = 'OK'

    report['verdict'] = verdict
    report['completed_at'] = datetime.now(timezone.utc).isoformat()

    logger.info(f'Diagnostic complete: {verdict}')
    logger.info(f'Report: {report}')

    return report


def main():
    """Main entry point."""
    report = run_diagnostic()

    logger.info('=' * 60)
    logger.info('DIAGNOSTIC REPORT: rug_pull_monitor heartbeat staleness')
    logger.info('=' * 60)
    logger.info(f'Started: {report["started_at"]}')
    logger.info(f'Completed: {report.get("completed_at", "N/A")}')
    logger.info(f'Verdict: {report["verdict"]}')
    logger.info('-' * 60)

    for finding in report['findings']:
        status_icon = '✓' if finding['status'] in ('PASS', 'OK') else ('!' if finding['status'] in ('WARNING', 'ELEVATED', 'HIGH') else '✗')
        logger.info(f'  [{status_icon}] {finding["check"]}: {finding["status"]}')
        if 'detail' in finding:
            detail = finding['detail']
            if isinstance(detail, dict):
                for k, v in detail.items():
                    logger.info(f'        {k}: {v}')
            else:
                logger.info(f'        {detail}')

    logger.info('-' * 60)
    logger.info(f'Summary: {report["summary"]}')
    logger.info('=' * 60)

    sys.exit(0)


if __name__ == '__main__':
    main()