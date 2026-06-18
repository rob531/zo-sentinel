#!/usr/bin/env python3
"""
diagnose_rug_pull_monitor_never_seen.py

Investigates why rug_pull_monitor has never heartbeat since being listed in
KNOWN_DAEMONS. Per spec §6: prefer diagnosis over rebuild.

Checks:
1. service_health for any rug_pull_monitor rows
2. supervisord status via subprocess
3. rug_pull_monitor.py exists on disk and is executable
4. NOT proposing rebuild (protected file per spec Appendix A)

Outputs diagnostic JSON with root_cause hypothesis and recovery steps.
"""
import json
import os
import subprocess
import sys
import requests
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# deps: requests

QUERY_URL = 'http://127.0.0.1:8772/query'


def ws_query(sql: str, params: list = None) -> Dict[str, Any]:
    """Execute SELECT against DuckDB via write_service /query."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if 'rows' in body and 'data' not in body:
        body['data'] = [[r[k] for k in r.keys()] for r in body['rows']]
    return body


def check_service_health() -> Dict[str, Any]:
    """Check service_health for rug_pull_monitor entries."""
    sql = """
    SELECT service, last_heartbeat, status, meta
    FROM service_health
    WHERE service LIKE '%rug_pull%'
    ORDER BY last_heartbeat DESC
    LIMIT 10
    """
    result = ws_query(sql)
    rows = result.get('data', [])
    return {
        'found': len(rows) > 0,
        'count': len(rows),
        'rows': [
            {
                'service': r[0],
                'last_heartbeat': r[1],
                'status': r[2],
                'meta': r[3]
            }
            for r in rows
        ]
    }


def check_supervisord() -> Dict[str, Any]:
    """Query supervisord status for rug_pull_monitor."""
    # First try supervisorctl status
    try:
        result = subprocess.run(
            ['supervisorctl', 'status'],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            rug_pull_entry = None
            all_services = []
            for line in lines:
                all_services.append(line)
                if 'rug_pull' in line.lower():
                    rug_pull_entry = line
            return {
                'supervisorctl_available': True,
                'rug_pull_registered': rug_pull_entry is not None,
                'rug_pull_entry': rug_pull_entry,
                'all_services': all_services
            }
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass

    # Check for supervisord processes
    try:
        result = subprocess.run(
            ['pgrep', '-fa', 'supervisord'],
            capture_output=True,
            text=True,
            timeout=5
        )
        supervisord_pids = result.stdout.strip().split('\n') if result.stdout.strip() else []
    except Exception:
        supervisord_pids = []

    return {
        'supervisorctl_available': False,
        'supervisord_pids': [p for p in supervisord_pids if p],
        'rug_pull_registered': False,
        'rug_pull_entry': None,
        'all_services': []
    }


def check_file_exists(path: str = '/home/workspace/zo_sentinel/rug_pull_monitor.py') -> Dict[str, Any]:
    """Verify rug_pull_monitor.py exists and is executable."""
    if not os.path.exists(path):
        return {
            'exists': False,
            'path': path,
            'is_file': False,
            'is_executable': False,
            'size': None,
            'mode': None
        }

    st = os.stat(path)
    return {
        'exists': True,
        'path': path,
        'is_file': os.path.isfile(path),
        'is_executable': os.access(path, os.X_OK),
        'size': st.st_size,
        'mode': oct(st.st_mode),
        'mtime': datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    }


def check_known_daemons() -> Dict[str, Any]:
    """Check if rug_pull_monitor is in KNOWN_DAEMONS."""
    # KNOWN_DAEMONS is typically a config constant; check for its presence
    # in sentinel config or related files
    search_paths = [
        '/home/workspace/zo_sentinel/daemon_registry.py',
        '/home/workspace/zo_sentinel/config.py',
        '/home/workspace/zo_sentinel/sentinel_config.py',
        '/etc/zo/supervisor.conf',
        '/etc/zo/supervisord-user.conf',
    ]

    results = {}
    for path in search_paths:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    content = f.read()
                has_known_daemons = 'KNOWN_DAEMONS' in content
                has_rug_pull = 'rug_pull_monitor' in content
                results[path] = {
                    'exists': True,
                    'has_known_daemons': has_known_daemons,
                    'has_rug_pull_monitor': has_rug_pull
                }
            except Exception as e:
                results[path] = {'exists': True, 'error': str(e)}
        else:
            results[path] = {'exists': False}

    return results


def diagnose() -> Dict[str, Any]:
    """Run all diagnostic checks and return findings."""
    ts = datetime.now(timezone.utc).isoformat()

    findings = {
        'timestamp': ts,
        'diagnostic': 'rug_pull_monitor_never_seen',
        'checks': {}
    }

    # Check 1: service_health
    try:
        findings['checks']['service_health'] = check_service_health()
    except Exception as e:
        findings['checks']['service_health'] = {'error': str(e)}

    # Check 2: supervisord
    try:
        findings['checks']['supervisord'] = check_supervisord()
    except Exception as e:
        findings['checks']['supervisord'] = {'error': str(e)}

    # Check 3: file existence
    findings['checks']['file_exists'] = check_file_exists()

    # Check 4: KNOWN_DAEMONS registration
    findings['checks']['known_daemons'] = check_known_daemons()

    # Determine root cause
    sh = findings['checks']['service_health']
    sv = findings['checks']['supervisord']
    fe = findings['checks']['file_exists']
    kd = findings['checks']['known_daemons']

    # Root cause analysis
    root_causes = []

    if not fe['exists']:
        root_causes.append({
            'cause': 'file_missing',
            'hypothesis': 'rug_pull_monitor.py does not exist on disk',
            'severity': 'CRITICAL'
        })
    elif not fe['is_executable']:
        root_causes.append({
            'cause': 'not_executable',
            'hypothesis': 'rug_pull_monitor.py lacks execute permission',
            'severity': 'HIGH'
        })

    if not sv.get('rug_pull_registered', False):
        if sv.get('supervisorctl_available'):
            root_causes.append({
                'cause': 'not_supervised',
                'hypothesis': 'rug_pull_monitor is NOT registered with supervisord - not in supervisor.conf',
                'severity': 'CRITICAL'
            })
        elif not sv.get('supervisord_pids'):
            root_causes.append({
                'cause': 'supervisord_not_running',
                'hypothesis': 'supervisord is not running - cannot supervise rug_pull_monitor',
                'severity': 'CRITICAL'
            })

    if not sh.get('found', False):
        root_causes.append({
            'cause': 'no_heartbeat',
            'hypothesis': 'No service_health entries for rug_pull_monitor - daemon never started',
            'severity': 'HIGH'
        })

    # Primary root cause (most likely)
    if root_causes:
        primary = root_causes[0]
    else:
        primary = {
            'cause': 'unknown',
            'hypothesis': 'Unable to determine root cause from available diagnostics',
            'severity': 'UNKNOWN'
        }

    findings['root_cause'] = primary
    findings['root_causes_all'] = root_causes

    # Recovery steps
    recovery_steps = []

    if not sv.get('rug_pull_registered', False) and sv.get('supervisorctl_available'):
        recovery_steps.append({
            'step': 1,
            'action': 'register_with_supervisord',
            'description': 'Add rug_pull_monitor to supervisord configuration',
            'command': 'supervisorctl reread && supervisorctl add rug_pull_monitor',
            'note': 'Requires adding [program:rug_pull_monitor] section to supervisor.conf'
        })
        recovery_steps.append({
            'step': 2,
            'action': 'start_daemon',
            'description': 'Start the rug_pull_monitor daemon',
            'command': 'supervisorctl start rug_pull_monitor',
            'note': 'After registration, start the process'
        })
    elif not sv.get('supervisord_pids'):
        recovery_steps.append({
            'step': 1,
            'action': 'start_supervisord',
            'description': 'Start supervisord service first',
            'command': 'supervisord -c /etc/zo/supervisord-user.conf',
            'note': 'Supervisord must be running before rug_pull_monitor can be supervised'
        })
        recovery_steps.append({
            'step': 2,
            'action': 'register_with_supervisord',
            'description': 'Register rug_pull_monitor with supervisord',
            'command': 'supervisorctl reread && supervisorctl add rug_pull_monitor',
            'note': 'Then register and start the daemon'
        })

    if not fe.get('is_executable', False):
        recovery_steps.append({
            'step': 99,
            'action': 'chmod_executable',
            'description': 'Make rug_pull_monitor.py executable',
            'command': 'chmod +x /home/workspace/zo_sentinel/rug_pull_monitor.py',
            'note': 'File exists but lacks execute permission'
        })

    if not recovery_steps:
        recovery_steps.append({
            'step': 1,
            'action': 'investigate_further',
            'description': 'Manual investigation required',
            'note': 'All standard checks passed but issue persists'
        })

    findings['recovery_steps'] = recovery_steps

    return findings


def main():
    """Run diagnostics and output JSON report."""
    try:
        findings = diagnose()
        print(json.dumps(findings, indent=2))
        return 0
    except Exception as e:
        error_report = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'diagnostic': 'rug_pull_monitor_never_seen',
            'error': str(e),
            'error_type': type(e).__name__
        }
        print(json.dumps(error_report, indent=2))
        return 1


if __name__ == '__main__':
    sys.exit(main())
