import os
import sys
import requests
import logging
from datetime import datetime, timezone
from pathlib import Path

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'rug_pull_monitor'
DIAGNOSTIC_NAME = 'rug_pull_monitor_staleness_diagnostic'
LOG_FILE = '/home/workspace/logs/rug_pull_monitor_staleness_diagnostic.log'

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def ws_write(table, rows):
    payload = {
        'table': table,
        'rows': rows,
        'wait': True
    }
    response = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def ws_query(sql, params=None):
    payload = {
        'sql': sql,
        'params': params or {}
    }
    response = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def read_source_file(filepath):
    try:
        with open(filepath, 'r') as f:
            return f.read()
    except Exception as e:
        logger.error(f"Failed to read source file {filepath}: {e}")
        return None

def check_process_running():
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                cmdline_str = ' '.join(cmdline)
                if 'rug_pull_monitor' in cmdline_str and '/home/workspace/' in cmdline_str:
                    if '/home/workspace/logs/' not in cmdline_str:
                        logger.info(f"Found running rug_pull_monitor process: PID={proc.info['pid']}")
                        return {'running': True, 'pid': proc.info['pid'], 'cmdline': cmdline_str}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        logger.warning("No running rug_pull_monitor process found")
        return {'running': False}
    except ImportError:
        logger.warning("psutil not available, skipping process check")
        return {'running': None}

def check_source_issues(source):
    issues = []
    if not source:
        issues.append("SOURCE_UNREADABLE: Could not read source file")
        return issues
    
    problem_patterns = [
        ('while True:', 'INFINITE_LOOP: while True without break condition'),
        ('time.sleep', 'SLEEP_PRESENT: Found time.sleep (may be ok if inside cycle)'),
        ('except:', 'BROAD_EXCEPTION: bare except clause swallows errors'),
        ('pass  # noop', 'NOOP_PASS: Found noop pass statement'),
        ('sys.exit', 'SYS_EXIT: Found sys.exit call'),
    ]
    
    lines = source.split('\n')
    in_finally = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if 'finally:' in stripped:
            in_finally = True
        if in_finally and stripped.startswith('return'):
            issues.append("RETURN_IN_FINALLY: return in finally block may skip cleanup")
        if stripped.startswith('finally:'):
            in_finally = False
        for pattern, issue_msg in problem_patterns:
            if pattern in stripped:
                if pattern == 'while True:' and i > 0:
                    prev_lines = '\n'.join(lines[max(0,i-10):i])
                    if 'cycle' in prev_lines.lower():
                        continue
                issues.append(f"{issue_msg} at line {i+1}: {stripped[:80]}")
    
    logger.info(f"Source analysis found {len(issues)} potential issues")
    return issues

def check_last_heartbeat():
    try:
        sql = """
        SELECT last_heartbeat, status, meta
        FROM service_health 
        WHERE service_name = 'rug_pull_monitor'
        ORDER BY last_heartbeat DESC 
        LIMIT 1
        """
        result = ws_query(sql)
        if result.get('rows'):
            row = result['rows'][0]
            logger.info(f"Last heartbeat: {row.get('last_heartbeat')}, status: {row.get('status')}, meta: {row.get('meta')}")
            return {
                'last_heartbeat': row.get('last_heartbeat'),
                'status': row.get('status'),
                'meta': row.get('meta')
            }
        else:
            logger.warning("No heartbeat record found for rug_pull_monitor")
            return None
    except Exception as e:
        logger.error(f"Failed to query heartbeat: {e}")
        return None

def check_error_logs():
    try:
        sql = """
        SELECT error_message, error_ts, context
        FROM service_health 
        WHERE service_name = 'rug_pull_monitor' 
        AND error_message IS NOT NULL
        ORDER BY error_ts DESC
        LIMIT 10
        """
        result = ws_query(sql)
        errors = result.get('rows', [])
        if errors:
            logger.info(f"Found {len(errors)} error log entries")
            return errors
        else:
            logger.info("No error logs found")
            return []
    except Exception as e:
        logger.error(f"Failed to query error logs: {e}")
        return []

def calculate_staleness(heartbeat_ts):
    if not heartbeat_ts:
        return None
    try:
        hb_dt = datetime.fromisoformat(heartbeat_ts.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = now - hb_dt.replace(tzinfo=timezone.utc)
        hours = delta.total_seconds() / 3600
        return hours
    except Exception as e:
        logger.error(f"Failed to parse heartbeat timestamp: {e}")
        return None

def run_diagnostics():
    logger.info("="*60)
    logger.info("Starting rug_pull_monitor staleness diagnostic")
    logger.info("="*60)
    
    results = {
        'diagnostic_name': DIAGNOSTIC_NAME,
        'diagnostic_ts': datetime.now(timezone.utc).isoformat(),
        'source_file': '/home/workspace/zo_sentinel/rug_pull_monitor.py',
        'source_issues': [],
        'process_status': None,
        'heartbeat_info': None,
        'staleness_hours': None,
        'error_logs': [],
        'verdict': None
    }
    
    source_path = Path('/home/workspace/zo_sentinel/rug_pull_monitor.py')
    source = read_source_file(source_path)
    results['source_issues'] = check_source_issues(source)
    
    results['process_status'] = check_process_running()
    
    heartbeat_info = check_last_heartbeat()
    results['heartbeat_info'] = heartbeat_info
    
    if heartbeat_info and heartbeat_info.get('last_heartbeat'):
        staleness = calculate_staleness(heartbeat_info['last_heartbeat'])
        results['staleness_hours'] = staleness
        logger.info(f"Calculated staleness: {staleness:.2f} hours")
    
    results['error_logs'] = check_error_logs()
    
    if not results['process_status']['running'] and results['process_status'].get('running') is not False:
        results['verdict'] = 'PROCESS_NOT_RUNNING'
    elif results['staleness_hours'] and results['staleness_hours'] > 24:
        results['verdict'] = 'STALE_HEARTBEAT'
    elif results['source_issues']:
        results['verdict'] = 'SOURCE_ISSUES'
    elif results['error_logs']:
        results['verdict'] = 'HAS_ERRORS'
    else:
        results['verdict'] = 'HEALTHY'
    
    logger.info("="*60)
    logger.info(f"Diagnostic verdict: {results['verdict']}")
    logger.info("="*60)
    logger.info(f"Staleness: {results['staleness_hours']:.2f} hours" if results['staleness_hours'] else "Staleness: unknown")
    logger.info(f"Process running: {results['process_status']}")
    logger.info(f"Source issues: {len(results['source_issues'])}")
    logger.info(f"Error logs: {len(results['error_logs'])}")
    
    if results['source_issues']:
        for issue in results['source_issues']:
            logger.warning(f"  ISSUE: {issue}")
    
    return results

if __name__ == '__main__':
    try:
        results = run_diagnostics()
        print(f"\nDIAGNOSTIC RESULTS:")
        print(f"  Verdict: {results['verdict']}")
        print(f"  Staleness: {results['staleness_hours']:.2f}h" if results['staleness_hours'] else "  Staleness: unknown")
        print(f"  Process running: {results['process_status']}")
        print(f"  Source issues: {len(results['source_issues'])}")
        print(f"  Error logs: {len(results['error_logs'])}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Diagnostic failed: {e}")
        sys.exit(1)