import logging
import os
import sys
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

SERVICE_NAME = 'stale_daemon_diagnostic'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_PATH = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

STALE_THRESHOLD_MINUTES = 30
CRITICAL_THRESHOLD_HOURS = 24


def ws_query(sql: str, params: Optional[tuple] = None) -> list:
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    response = requests.post(
        WRITE_SERVICE_URL + '/query',
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    result = response.json()
    return result.get('rows', [])


def calculate_age_minutes(last_heartbeat: str) -> float:
    try:
        if last_heartbeat.endswith('Z'):
            last_heartbeat = last_heartbeat[:-1]
        heartbeat_dt = datetime.fromisoformat(last_heartbeat).replace(tzinfo=timezone.utc)
        now_dt = datetime.now(timezone.utc)
        delta = now_dt - heartbeat_dt
        return delta.total_seconds() / 60.0
    except (ValueError, TypeError):
        return float('inf')


def diagnose_stale_reason(age_minutes: float, service_name: str) -> str:
    if age_minutes < 30:
        return "healthy"
    elif age_minutes < 60:
        return "delayed - possible network latency or high load"
    elif age_minutes < 120:
        return "concerning - possible slow cycle or queued backlog"
    elif age_minutes < 240:
        return "degraded - likely daemon unresponsive, check logs for crashes"
    elif age_minutes < 480:
        return "critical - daemon likely crashed, check process status and error logs"
    else:
        return "FROZEN - daemon dead for extended period, requires manual intervention"


def check_process_exists(service_name: str) -> tuple[bool, Optional[str]]:
    canonical_paths = {
        'write_service': '/home/workspace/zo_sentinel/write_service.py',
        'mcp_scanner': '/home/workspace/zo_sentinel/mcp_scanner.py',
        'anti_entropy': '/home/workspace/zo_sentinel/anti_entropy_agent.py',
        'wisdom_synthesiser': '/home/workspace/zo_sentinel/wisdom_synthesiser.py',
        'rug_pull_monitor': '/home/workspace/zo_sentinel/rug_pull_monitor.py',
    }
    path = canonical_paths.get(service_name)
    if not path:
        return False, None
    try:
        result = os.popen(f"pgrep -f '{path}' 2>/dev/null").read().strip()
        if result:
            return True, result
        return False, None
    except Exception:
        return False, None


def build_diagnostic_report():
    logger.info("Starting stale daemon heartbeat diagnostic report")
    
    sql = """
    SELECT service_name, status, last_heartbeat, meta
    FROM service_health
    ORDER BY last_heartbeat ASC
    """
    
    try:
        rows = ws_query(sql)
    except Exception as e:
        logger.error(f"Failed to query service_health: {e}")
        print(f"FATAL: Cannot connect to write_service at {WRITE_SERVICE_URL}")
        print("Possible causes: write_service down, network blocked, authentication failure")
        sys.exit(1)
    
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("STALE DAEMON HEARTBEAT DIAGNOSTIC REPORT")
    report_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    report_lines.append(f"Stale threshold: {STALE_THRESHOLD_MINUTES} minutes")
    report_lines.append(f"Critical threshold: {CRITICAL_THRESHOLD_HOURS} hours")
    report_lines.append("=" * 80)
    report_lines.append("")
    
    stale_count = 0
    critical_count = 0
    healthy_count = 0
    unknown_count = 0
    
    for row in rows:
        service_name = row.get('service_name', 'unknown')
        status = row.get('status', 'unknown')
        last_heartbeat = row.get('last_heartbeat', '')
        meta = row.get('meta', '')
        
        age_minutes = calculate_age_minutes(last_heartbeat)
        reason = diagnose_stale_reason(age_minutes, service_name)
        
        is_stale = age_minutes >= STALE_THRESHOLD_MINUTES
        is_critical = age_minutes >= (CRITICAL_THRESHOLD_HOURS * 60)
        
        if is_critical:
            critical_count += 1
        elif is_stale:
            stale_count += 1
        elif service_name != 'unknown':
            healthy_count += 1
        else:
            unknown_count += 1
        
        marker = "🔴 CRITICAL" if is_critical else ("⚠️  STALE" if is_stale else "✅ OK")
        
        if age_minutes == float('inf'):
            age_str = "NEVER RECORDED"
        elif age_minutes < 60:
            age_str = f"{age_minutes:.1f} minutes"
        elif age_minutes < 1440:
            hours = age_minutes / 60
            age_str = f"{hours:.1f} hours"
        else:
            days = age_minutes / 1440
            age_str = f"{days:.1f} days"
        
        running, pid_info = check_process_exists(service_name)
        process_status = f"PID(s): {pid_info}" if running else "NOT RUNNING"
        
        report_lines.append(f"{marker} {service_name}")
        report_lines.append(f"  Last heartbeat: {last_heartbeat}")
        report_lines.append(f"  Age: {age_str}")
        report_lines.append(f"  Status field: {status}")
        report_lines.append(f"  Process: {process_status}")
        report_lines.append(f"  Diagnosis: {reason}")
        if meta:
            report_lines.append(f"  Meta: {meta}")
        report_lines.append("")
    
    report_lines.append("=" * 80)
    report_lines.append("SUMMARY")
    report_lines.append("=" * 80)
    report_lines.append(f"  Total services monitored: {len(rows)}")
    report_lines.append(f"  Healthy: {healthy_count}")
    report_lines.append(f"  Stale (>30m): {stale_count}")
    report_lines.append(f"  Critical (>24h): {critical_count}")
    report_lines.append(f"  Unknown: {unknown_count}")
    report_lines.append("")
    
    if critical_count > 0:
        report_lines.append("⚠️  ACTION REQUIRED: One or more services have been stale for >24 hours.")
        report_lines.append("   Check process status: pgrep -f '/home/workspace/zo_sentinel/'")
        report_lines.append("   Check error logs in /home/workspace/logs/")
        report_lines.append("   Consider restarting affected services.")
    elif stale_count > 0:
        report_lines.append("⚠️  WARNING: Some services are stale but may recover.")
        report_lines.append("   Monitor for next 30-60 minutes.")
    else:
        report_lines.append("✅ All services appear healthy.")
    
    report_lines.append("")
    report_lines.append("Diagnostic complete.")
    
    return "\n".join(report_lines)


if __name__ == '__main__':
    logger.info("Stale daemon diagnostic invoked")
    report = build_diagnostic_report()
    print(report)
    logger.info("Diagnostic report complete, exiting")
    sys.exit(0)