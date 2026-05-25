#!/usr/bin/env python3
"""
gate_orchestrator_error_probe.py
Investigates gate_orchestrator daemon ERROR state.
"""
import logging
import os
import sys
import requests
from datetime import datetime, timezone

# Constants
WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'gate_orchestrator_error_probe'
GATE_ORCHESTRATOR_DAEMON_PATH = '/home/workspace/zo_sentinel/gate_orchestrator.py'
GATE_ORCHESTRATOR_LOG_PATH = '/home/workspace/logs/gate_orchestrator.log'

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def ws_query(sql):
    """Execute read query via write_service HTTP endpoint."""
    payload = {'sql': sql, 'wait': True}
    response = requests.post(
        WRITE_SERVICE_URL + '/query',
        json=payload,
        timeout=15
    )
    response.raise_for_status()
    return response.json()


def check_daemon_exists():
    """Check if gate_orchestrator.py exists on disk."""
    return os.path.isfile(GATE_ORCHESTRATOR_DAEMON_PATH)


def read_recent_log_lines(path, num_lines=100):
    """Read last N lines from a log file."""
    try:
        if not os.path.isfile(path):
            return None
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
        if len(lines) <= num_lines:
            return ''.join(lines)
        return ''.join(lines[-num_lines:])
    except Exception as e:
        return f'[Error reading log: {e}]'


def probe():
    """Main investigation logic."""
    logger.info('=== Gate Orchestrator Error Investigation ===')
    findings = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'error_state': {},
        'daemon_exists': False,
        'daemon_path': GATE_ORCHESTRATOR_DAEMON_PATH,
        'log_excerpt': None,
        'recommendation': None
    }

    # (a) Query service_health for gate_orchestrator status
    logger.info('Querying service_health for gate_orchestrator state...')
    try:
        result = ws_query(
            "SELECT service_name, status, last_heartbeat, last_error, meta "
            "FROM service_health WHERE service_name = 'gate_orchestrator' "
            "ORDER BY last_heartbeat DESC LIMIT 1"
        )
        if result and len(result) > 0:
            row = result[0]
            findings['error_state'] = {
                'status': row.get('status', 'UNKNOWN'),
                'last_heartbeat': row.get('last_heartbeat', 'N/A'),
                'last_error': row.get('last_error', row.get('error', 'N/A')),
                'meta': row.get('meta', {})
            }
            logger.info(f"Current state: status={findings['error_state']['status']}, "
                       f"last_error={findings['error_state']['last_error']}")
        else:
            findings['error_state'] = {
                'status': 'NOT_FOUND',
                'last_heartbeat': 'N/A',
                'last_error': 'No entry in service_health table'
            }
            logger.warning('No service_health entry for gate_orchestrator found.')
    except Exception as e:
        logger.error(f'service_health query failed: {e}')
        findings['error_state'] = {'query_failed': str(e)}

    # (b) Check daemon file existence
    logger.info('Checking daemon file existence...')
    findings['daemon_exists'] = check_daemon_exists()
    logger.info(f'Daemon file exists: {findings["daemon_exists"]}')

    # (c) Read recent log excerpt
    logger.info('Reading recent log lines...')
    findings['log_excerpt'] = read_recent_log_lines(GATE_ORCHESTRATOR_LOG_PATH, 100)
    if findings['log_excerpt']:
        logger.info('Log excerpt retrieved successfully')
    else:
        logger.warning('No log file found or log is empty')

    # (d) Generate recommendation
    status = findings['error_state'].get('status', 'UNKNOWN')
    error_msg = findings['error_state'].get('last_error', '')
    daemon_ok = findings['daemon_exists']

    if not daemon_ok:
        findings['recommendation'] = (
            'CRITICAL: gate_orchestrator.py not found at expected path. '
            'Daemon must be rebuilt and deployed before monitoring can proceed.'
        )
    elif status == 'RUNNING':
        findings['recommendation'] = (
            'Daemon appears healthy. If ERROR state was reported, check if stale '
            'entry is present in service_health. Monitor for 5 minutes.'
        )
    elif status == 'ERROR':
        rec = 'Daemon in ERROR state. '
        if 'Traceback' in str(error_msg) or 'Error' in str(error_msg):
            rec += 'Python exception detected. Review log excerpt above for details.'
        else:
            rec += 'Check log file for traceback. Common causes: import failure, '
            rec += 'write_service unreachable, configuration error.'
        rec += ' Consider restarting via: python -m zo_sentinel.gate_orchestrator'
        findings['recommendation'] = rec
    else:
        findings['recommendation'] = (
            f'Unknown state: {status}. Manual investigation required.'
        )

    # Print findings
    print('\n=== GATE ORCHESTRATOR ERROR INVESTIGATION RESULTS ===')
    print(f"Timestamp: {findings['timestamp']}")
    print(f"\n(a) Service Health State:")
    print(f"    Status: {findings['error_state'].get('status', 'N/A')}")
    print(f"    Last Heartbeat: {findings['error_state'].get('last_heartbeat', 'N/A')}")
    print(f"    Last Error: {findings['error_state'].get('last_error', 'N/A')}")
    print(f"\n(b) Daemon File:")
    print(f"    Path: {findings['daemon_path']}")
    print(f"    Exists: {findings['daemon_exists']}")
    print(f"\n(c) Recent Log ({GATE_ORCHESTRATOR_LOG_PATH}):")
    if findings['log_excerpt']:
        print('--- BEGIN LOG EXCERPT ---')
        print(findings['log_excerpt'][:3000])
        print('--- END LOG EXCERPT ---')
    else:
        print('    [No log data available]')
    print(f"\n(d) Recommendation:")
    print(f"    {findings['recommendation']}")
    print('\n=== END INVESTIGATION ===\n')

    logger.info('Investigation complete')
    return findings


if __name__ == '__main__':
    try:
        probe()
        sys.exit(0)
    except Exception as e:
        logger.error(f'Probe failed: {e}')
        sys.exit(1)