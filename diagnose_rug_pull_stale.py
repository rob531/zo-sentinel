#!/usr/bin/env python3
"""
diagnose_rug_pull_stale.py -- Diagnostic for rug_pull_monitor stale heartbeat.
Investigates why rug_pull_monitor has 609h51m stale heartbeat.
"""
import os
import sys
import json
import time
import hashlib
import signal
import logging
import requests
from datetime import datetime, timezone

# Constants
SERVICE_NAME = 'diagnose_rug_pull_stale'
WRITE_SERVICE_URL = 'http://localhost:8772'

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# PID file location for rug_pull_monitor
RUG_PULL_MONITOR_PID_FILE = '/home/workspace/zo_sentinel/rug_pull_monitor.pid'
RUG_PULL_MONITOR_CANONICAL_PATH = '/home/workspace/zo_sentinel/rug_pull_monitor.py'


def ws_write(table, rows):
    """Helper: write rows to DuckDB via write_service."""
    payload = {
        'table': table,
        'rows': rows,
        'wait': True
    }
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql, params=None):
    """Helper: query DuckDB via write_service."""
    payload = {'sql': sql, 'params': params, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_rug_pull_monitor_health():
    """Query service_health table for rug_pull_monitor last heartbeat."""
    sql = """
    SELECT service_name, status, last_heartbeat, meta
    FROM service_health
    WHERE service_name = 'rug_pull_monitor'
    ORDER BY last_heartbeat DESC
    LIMIT 1
    """
    result = ws_query(sql)
    return result


def check_pid_file():
    """Check if PID file exists and contains valid PID."""
    if not os.path.exists(RUG_PULL_MONITOR_PID_FILE):
        return {'exists': False, 'pid': None, 'reason': 'PID file does not exist'}
    
    try:
        with open(RUG_PULL_MONITOR_PID_FILE, 'r') as f:
            pid_str = f.read().strip()
            pid = int(pid_str)
        
        # Check if process exists
        try:
            os.kill(pid, 0)  # Signal 0 just checks if process exists
            return {'exists': True, 'pid': pid, 'alive': True, 'reason': 'Process is running'}
        except OSError:
            return {'exists': True, 'pid': pid, 'alive': False, 'reason': 'PID file exists but process is dead'}
    except (ValueError, IOError) as e:
        return {'exists': True, 'pid': None, 'reason': f'Error reading PID file: {e}'}


def check_process_by_pgrep():
    """Check if rug_pull_monitor process is running using pgrep."""
    import subprocess
    try:
        # Use pgrep to find the canonical path, exclude log files
        result = subprocess.run(
            ['pgrep', '-f', RUG_PULL_MONITOR_CANONICAL_PATH],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(p) for p in result.stdout.strip().split('\n') if p]
            return {'running': True, 'pids': pids, 'reason': f'Found {len(pids)} process(es)'}
        else:
            return {'running': False, 'pids': [], 'reason': 'No process found matching canonical path'}
    except Exception as e:
        return {'running': False, 'pids': [], 'reason': f'pgrep failed: {e}'}


def check_proc_status(pid):
    """Check /proc/<pid>/status for process state."""
    if pid is None:
        return None
    
    status_path = f'/proc/{pid}/status'
    if not os.path.exists(status_path):
        return {'exists': False, 'state': 'unknown'}
    
    try:
        with open(status_path, 'r') as f:
            lines = f.readlines()
        
        state_info = {}
        for line in lines:
            if line.startswith(('Name:', 'State:', 'PPid:', 'Uid:')):
                key, val = line.split(':', 1)
                state_info[key.strip()] = val.strip()
        
        return {'exists': True, 'state': state_info.get('State', 'unknown'), 'info': state_info}
    except IOError:
        return {'exists': False, 'state': 'unknown'}


def check_log_file():
    """Check the rug_pull_monitor log file for recent activity."""
    log_path = f'/home/workspace/logs/rug_pull_monitor.log'
    if not os.path.exists(log_path):
        return {'exists': False, 'last_line': None, 'age_seconds': None}
    
    try:
        stat = os.stat(log_path)
        mtime = stat.st_mtime
        age_seconds = time.time() - mtime
        age_hours = age_seconds / 3600
        
        # Read last few lines
        with open(log_path, 'r') as f:
            lines = f.readlines()
        
        last_lines = lines[-10:] if len(lines) > 10 else lines
        last_line = last_lines[-1].strip() if last_lines else None
        
        return {
            'exists': True,
            'size_bytes': stat.st_size,
            'age_seconds': age_seconds,
            'age_hours': age_hours,
            'last_line': last_line,
            'line_count': len(lines)
        }
    except IOError as e:
        return {'exists': False, 'error': str(e)}


def compute_staleness(heartbeat_iso):
    """Compute how stale the heartbeat is."""
    if heartbeat_iso is None:
        return {'stale': True, 'reason': 'No heartbeat recorded'}
    
    try:
        if heartbeat_iso.endswith('Z'):
            heartbeat_iso = heartbeat_iso[:-1]
        last_hb = datetime.fromisoformat(heartbeat_iso).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - last_hb
        hours = delta.total_seconds() / 3600
        
        return {
            'stale': hours > 24,
            'hours': hours,
            'last_heartbeat': heartbeat_iso,
            'delta_seconds': delta.total_seconds()
        }
    except Exception as e:
        return {'stale': True, 'reason': f'Parse error: {e}'}


def main():
    """Run diagnostics and report findings."""
    logger.info("=== Starting rug_pull_monitor stale heartbeat diagnostic ===")
    
    findings = {
        'diagnostic_time': datetime.now(timezone.utc).isoformat(),
        'service_name': 'rug_pull_monitor'
    }
    
    # 1. Check service_health table
    logger.info("Checking service_health table...")
    health_result = get_rug_pull_monitor_health()
    findings['service_health_query'] = health_result
    
    last_heartbeat = None
    if health_result.get('rows') and len(health_result['rows']) > 0:
        row = health_result['rows'][0]
        findings['heartbeat_record'] = row
        last_heartbeat = row.get('last_heartbeat')
        logger.info(f"Found heartbeat record: {row}")
    else:
        logger.warning("No heartbeat record found in service_health table")
        findings['heartbeat_record'] = None
    
    # 2. Compute staleness
    staleness = compute_staleness(last_heartbeat)
    findings['staleness'] = staleness
    logger.info(f"Staleness analysis: {staleness}")
    
    # 3. Check PID file
    logger.info("Checking PID file...")
    pid_info = check_pid_file()
    findings['pid_file'] = pid_info
    logger.info(f"PID file status: {pid_info}")
    
    # 4. Check process via pgrep
    logger.info("Checking process via pgrep...")
    pgrep_result = check_process_by_pgrep()
    findings['pgrep'] = pgrep_result
    logger.info(f"pgrep result: {pgrep_result}")
    
    # 5. Check /proc status if we have a PID
    if pid_info.get('pid'):
        proc_status = check_proc_status(pid_info['pid'])
        findings['proc_status'] = proc_status
        logger.info(f"Proc status: {proc_status}")
    
    # 6. Check log file
    logger.info("Checking log file...")
    log_info = check_log_file()
    findings['log_file'] = log_info
    logger.info(f"Log file status: {log_info}")
    
    # Summary diagnosis
    is_stale = staleness.get('stale', True)
    process_alive = pgrep_result.get('running', False)
    
    if is_stale:
        if process_alive:
            findings['diagnosis'] = "PROCESS RUNNING but heartbeat stale - possible write_service issue or loop stuck"
        else:
            findings['diagnosis'] = "PROCESS DEAD and heartbeat stale - daemon crashed or was killed"
    else:
        findings['diagnosis'] = "Heartbeat appears current - staleness may be display artifact"
    
    logger.info(f"Final diagnosis: {findings['diagnosis']}")
    
    # Write findings to service_health for this diagnostic
    diagnostic_row = {
        'service_name': SERVICE_NAME,
        'status': 'completed',
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps(findings)
    }
    ws_write('service_health', [diagnostic_row])
    
    logger.info("=== Diagnostic complete ===")
    logger.info(f"Summary: {findings['diagnosis']}")
    
    # Print summary to stdout for easy reading
    print("\n" + "="*60)
    print("RUG_PULL_MONITOR STALE HEARTBEAT DIAGNOSTIC")
    print("="*60)
    print(f"Diagnosis: {findings['diagnosis']}")
    print(f"Heartbeat: {last_heartbeat or 'NONE'}")
    print(f"Staleness: {staleness.get('hours', 'N/A')} hours" if 'hours' in staleness else f"Staleness: {staleness.get('reason')}")
    print(f"Process running: {process_alive}")
    print(f"Log file age: {log_info.get('age_hours', 'N/A')} hours" if log_info.get('exists') else "Log file: not found")
    print("="*60)
    
    sys.exit(0)


if __name__ == '__main__':
    main()