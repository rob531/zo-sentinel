#!/usr/bin/env python3
"""
diagnose_attestation_engine_stale.py
Diagnostic-only module for attestation_engine staleness investigation.
DOES NOT rebuild or modify attestation_engine.py
"""

import os
import sys
import time
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

import requests

SERVICE_NAME = 'diagnose_attestation_engine_stale'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_INTERVAL = 60
STALENESS_THRESHOLD_SECS = 28800
LOG_DIR = '/home/workspace/zo_sentinel/logs'
LOG_FILE = f'{LOG_DIR}/attestation_engine.log'
DUCKDB_PATH = '/tmp/zo_sentinel/mcp_sentinel.db'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f'{LOG_DIR}/diagnose_attestation_engine_stale.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(SERVICE_NAME)


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ws_query(sql: str, params: Optional[List] = None) -> Optional[Dict]:
    try:
        payload = {'sql': sql}
        if params:
            payload['params'] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return None


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return False


def get_service_health(service: str) -> Optional[Dict]:
    sql = f"SELECT service, last_heartbeat FROM service_health WHERE service = '{service}'"
    result = ws_query(sql)
    if result and result.get('rows'):
        return result['rows'][0]
    return None


def calculate_heartbeat_age(heartbeat: Optional[str]) -> Optional[float]:
    if not heartbeat:
        return None
    try:
        hb_time = datetime.fromisoformat(heartbeat.replace('Z', '+00:00'))
        age = (get_utc_now() - hb_time).total_seconds()
        return age
    except Exception as e:
        logger.error(f"Failed to parse heartbeat: {e}")
        return None


def read_log_tail(filepath: str, lines: int = 200) -> List[str]:
    try:
        if not os.path.exists(filepath):
            return [f"Log file not found: {filepath}"]
        with open(filepath, 'r') as f:
            all_lines = f.readlines()
        return all_lines[-lines:] if len(all_lines) > lines else all_lines
    except Exception as e:
        return [f"Failed to read log: {e}"]


def parse_last_attestation_time(log_lines: List[str]) -> Optional[str]:
    for line in reversed(log_lines):
        if 'attestation written' in line.lower() or 'attestation generated' in line.lower() or 'completed attestation' in line.lower():
            parts = line.split(' - ', 2)
            if len(parts) >= 3:
                return parts[0]
    return None


def detect_error_patterns(log_lines: List[str]) -> Dict[str, List[str]]:
    patterns = {
        'write_failure': [],
        'on_conflict_error': [],
        'llm_timeout': [],
        'inference_router_error': [],
        'attestation_text_failure': [],
        'connection_error': []
    }
    for line in log_lines:
        lower = line.lower()
        if 'write' in lower and ('fail' in lower or 'error' in lower or 'exception' in lower):
            patterns['write_failure'].append(line.strip())
        if 'on conflict' in lower:
            patterns['on_conflict_error'].append(line.strip())
        if 'timeout' in lower and ('llm' in lower or 'inference' in lower or '8773' in lower):
            patterns['llm_timeout'].append(line.strip())
        if '8773' in lower and ('error' in lower or 'fail' in lower or 'timeout' in lower):
            patterns['inference_router_error'].append(line.strip())
        if 'attestation' in lower and ('fail' in lower or 'error' in lower or 'exception' in lower):
            patterns['attestation_text_failure'].append(line.strip())
        if 'connection' in lower and ('refused' in lower or 'timeout' in lower or 'unreachable' in lower):
            patterns['connection_error'].append(line.strip())
    return patterns


def get_attestations_count_trend() -> List[Dict]:
    sql = """
    SELECT 
        DATE(created_at) as attest_date,
        COUNT(*) as count
    FROM mcp_attestations 
    WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY DATE(created_at)
    ORDER BY attest_date DESC
    LIMIT 10
    """
    result = ws_query(sql)
    if result and result.get('rows'):
        return result['rows']
    return []


def get_recent_attestations(limit: int = 10) -> List[Dict]:
    sql = f"""
    SELECT server_id, mcp_name, verdict, created_at 
    FROM mcp_attestations 
    ORDER BY created_at DESC 
    LIMIT {limit}
    """
    result = ws_query(sql)
    if result and result.get('rows'):
        return result['rows']
    return []


def check_inference_router_health() -> Dict:
    try:
        resp = requests.get('http://127.0.0.1:8773/health', timeout=5)
        if resp.status_code == 200:
            return {'status': 'ok', 'details': resp.json()}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}
    return {'status': 'unknown'}


def check_write_service_health() -> Dict:
    try:
        resp = requests.get('http://127.0.0.1:8772/health', timeout=5)
        if resp.status_code == 200:
            return {'status': 'ok', 'details': resp.json()}
    except Exception as e:
        return {'status': 'error', 'error': str(e)}
    return {'status': 'unknown'}


def check_process_uptime() -> Optional[int]:
    pid_file = '/tmp/attestation_engine.pid'
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            import subprocess
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'etime='], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
    return None


def check_supervisord_status() -> Optional[Dict]:
    try:
        import xmlrpc.client
        supervisor = xmlrpc.client.ServerProxy('http://127.0.0.1:9001/RPC2')
        state = supervisor.getProcessInfo('attestation_engine')
        return {
            'pid': state.get('pid'),
            'state': state.get('state'),
            'uptime': state.get('uptime')
        }
    except Exception as e:
        logger.error(f"Supervisord check failed: {e}")
        return None


def diagnose() -> Dict[str, Any]:
    logger.info("=" * 60)
    logger.info("ATTESTATION ENGINE STALENESS DIAGNOSTIC")
    logger.info("=" * 60)
    
    findings = {
        'diagnostic_time': get_utc_now().isoformat(),
        'staleness_threshold_secs': STALENESS_THRESHOLD_SECS,
        'service_health': {},
        'heartbeat_age': None,
        'is_stale': False,
        'log_analysis': {},
        'error_patterns': {},
        'attestations_trend': [],
        'recent_attestations': [],
        'inference_router_health': {},
        'write_service_health': {},
        'process_uptime': None,
        'supervisord_status': {},
        'root_causes': [],
        'recommendations': []
    }
    
    health = get_service_health('attestation_engine')
    findings['service_health'] = health
    
    if health and health.get('last_heartbeat'):
        age = calculate_heartbeat_age(health['last_heartbeat'])
        findings['heartbeat_age'] = age
        findings['is_stale'] = age > STALENESS_THRESHOLD_SECS if age else True
        age_mins = age / 60 if age else 0
        age_hrs = age / 3600 if age else 0
        logger.info(f"Last heartbeat: {health['last_heartbeat']} | Age: {age_hrs:.2f}h ({age_mins:.1f}m)")
        if findings['is_stale']:
            findings['root_causes'].append(f"Heartbeat is {age_hrs:.2f} hours old - process may be hung or crashed")
    else:
        findings['root_causes'].append("No heartbeat record found for attestation_engine")
    
    log_lines = read_log_tail(LOG_FILE, 500)
    findings['log_analysis'] = {
        'total_lines_read': len(log_lines),
        'last_attestation_time': parse_last_attestation_time(log_lines),
        'recent_lines': log_lines[-20:]
    }
    
    error_patterns = detect_error_patterns(log_lines)
    findings['error_patterns'] = error_patterns
    
    for pattern_name, errors in error_patterns.items():
        if errors:
            logger.warning(f"Found {len(errors)} {pattern_name} patterns")
            findings['root_causes'].append(f"{pattern_name}: {len(errors)} instances detected")
    
    attestations_trend = get_attestations_count_trend()
    findings['attestations_trend'] = attestations_trend
    
    recent_attestations = get_recent_attestations(5)
    findings['recent_attestations'] = recent_attestations
    
    if not recent_attestations:
        findings['root_causes'].append("No recent attestations found in mcp_attestations table")
    
    findings['inference_router_health'] = check_inference_router_health()
    findings['write_service_health'] = check_write_service_health()
    
    findings['process_uptime'] = check_process_uptime()
    findings['supervisord_status'] = check_supervisord_status() or {}
    
    if findings['inference_router_health'].get('status') != 'ok':
        findings['root_causes'].append("inference_router (port 8773) is not healthy - LLM attestation generation may fail")
        findings['recommendations'].append("Check inference_router health: curl http://127.0.0.1:8773/health")
    
    if findings['write_service_health'].get('status') != 'ok':
        findings['root_causes'].append("write_service (port 8772) is not healthy - attestation writes will fail")
        findings['recommendations'].append("Check write_service health: curl http://127.0.0.1:8772/health")
    
    write_failures = error_patterns.get('write_failure', [])
    if write_failures:
        findings['root_causes'].append(f"Database write failures detected: {len(write_failures)} occurrences")
        findings['recommendations'].append("Check write_service connectivity and DuckDB permissions")
    
    llm_timeouts = error_patterns.get('llm_timeout', [])
    if llm_timeouts:
        findings['root_causes'].append(f"LLM inference timeouts to inference_router: {len(llm_timeouts)} occurrences")
        findings['recommendations'].append("Verify inference_router is responding to attestation generation requests")
    
    on_conflict_errors = error_patterns.get('on_conflict_error', [])
    if on_conflict_errors:
        findings['root_causes'].append(f"ON CONFLICT handling errors detected: {len(on_conflict_errors)} occurrences")
        findings['recommendations'].append("Check attestation_engine ON CONFLICT DO NOTHING/DO UPDATE syntax")
    
    attestation_failures = error_patterns.get('attestation_text_failure', [])
    if attestation_failures:
        findings['root_causes'].append(f"Attestation text generation failures: {len(attestation_failures)} occurrences")
        findings['recommendations'].append("Check LLM inference_router response format and attestation templates")
    
    connection_errors = error_patterns.get('connection_error', [])
    if connection_errors:
        findings['root_causes'].append(f"Connection errors detected: {len(connection_errors)} occurrences")
        findings['recommendations'].append("Check network connectivity to required services")
    
    if not findings['root_causes']:
        findings['root_causes'].append("No specific error pattern identified - process may be sleeping or waiting on external resource")
        findings['recommendations'].append("Check process state with: supervisorctl status attestation_engine")
        findings['recommendations'].append("Review full log for recent activity: tail -f /home/workspace/zo_sentinel/logs/attestation_engine.log")
    
    findings['recommendations'].append("If process is crashed, restart with: supervisorctl restart attestation_engine")
    findings['recommendations'].append("Check CYCLE_INTERVAL in attestation_engine.py - may be set to very long period")
    
    return findings


def print_report(findings: Dict):
    print("\n" + "=" * 70)
    print("ATTESTATION ENGINE STALENESS DIAGNOSTIC REPORT")
    print("=" * 70)
    
    print(f"\nDiagnostic Time: {findings['diagnostic_time']}")
    print(f"Staleness Threshold: {findings['staleness_threshold_secs']}s ({findings['staleness_threshold_secs']/3600:.1f}h)")
    
    print("\n--- SERVICE HEALTH ---")
    health = findings['service_health']
    if health:
        print(f"  Last Heartbeat: {health.get('last_heartbeat', 'N/A')}")
    else:
        print("  Last Heartbeat: NO RECORD FOUND")
    
    age = findings.get('heartbeat_age')
    if age:
        print(f"  Heartbeat Age: {age:.1f}s ({age/60:.1f}m, {age/3600:.2f}h)")
        print(f"  Is Stale: {findings['is_stale']}")
    
    uptime = findings.get('process_uptime')
    if uptime:
        print(f"  Process Uptime: {uptime}")
    
    sup = findings.get('supervisord_status', {})
    if sup:
        print(f"  Supervisord State: {sup.get('state', 'N/A')} (PID: {sup.get('pid', 'N/A')})")
    
    print("\n--- ERROR PATTERNS DETECTED ---")
    error_patterns = findings.get('error_patterns', {})
    total_errors = sum(len(v) for v in error_patterns.values())
    if total_errors == 0:
        print("  No error patterns detected in recent logs")
    else:
        for pattern, lines in error_patterns.items():
            if lines:
                print(f"\n  [{pattern.upper()}] - {len(lines)} occurrences:")
                for line in lines[:5]:
                    print(f"    {line[:120]}")
                if len(lines) > 5:
                    print(f"    ... and {len(lines) - 5} more")
    
    print("\n--- ATTESTATIONS TREND (last 7 days) ---")
    trend = findings.get('attestations_trend', [])
    if trend:
        for entry in trend:
            print(f"  {entry.get('attest_date', 'N/A')}: {entry.get('count', 0)} attestations")
    else:
        print("  No attestation data in last 7 days")
    
    print("\n--- RECENT ATTESTATIONS ---")
    recent = findings.get('recent_attestations', [])
    if recent:
        for entry in recent:
            print(f"  {entry.get('created_at', 'N/A')}: {entry.get('mcp_name', 'N/A')} -> {entry.get('verdict', 'N/A')}")
    else:
        print("  No recent attestations found")
    
    print("\n--- DEPENDENCY HEALTH ---")
    ir_health = findings.get('inference_router_health', {})
    print(f"  inference_router (8773): {ir_health.get('status', 'unknown')}")
    if ir_health.get('details'):
        print(f"    Details: {ir_health['details']}")
    if ir_health.get('error'):
        print(f"    Error: {ir_health['error']}")
    
    ws_health = findings.get('write_service_health', {})
    print(f"  write_service (8772): {ws_health.get('status', 'unknown')}")
    if ws_health.get('details'):
        print(f"    Details: {ws_health['details']}")
    if ws_health.get('error'):
        print(f"    Error: {ws_health['error']}")
    
    print("\n--- ROOT CAUSES ---")
    for i, cause in enumerate(findings['root_causes'], 1):
        print(f"  {i}. {cause}")
    
    print("\n--- RECOMMENDATIONS ---")
    for i, rec in enumerate(findings['recommendations'], 1):
        print(f"  {i}. {rec}")
    
    print("\n" + "=" * 70)


def save_report(findings: Dict):
    report_path = f"{LOG_DIR}/diagnostic_attestation_engine_{int(time.time())}.json"
    try:
        with open(report_path, 'w') as f:
            json.dump(findings, f, indent=2, default=str)
        logger.info(f"Report saved to {report_path}")
    except Exception as e:
        logger.error(f"Failed to save report: {e}")


def run():
    logger.info("Starting attestation engine staleness diagnostic")
    
    findings = diagnose()
    print_report(findings)
    save_report(findings)
    
    return findings


if __name__ == '__main__':
    findings = run()
    sys.exit(0 if not findings['is_stale'] else 1)