import logging
import time
import os
import requests
from datetime import datetime, timezone

SERVICE_NAME = "gate_orchestrator"
STALE_THRESHOLD_SECONDS = 28800
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"

LOG_DIR = "/var/log/zo_sentinel"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_service_health():
    health_url = "http://127.0.0.1:8772/query"
    query_payload = {
        "sql": f"SELECT * FROM service_health WHERE service = '{SERVICE_NAME}'",
        "params": {}
    }
    response = requests.post(health_url, json=query_payload, timeout=10)
    response.raise_for_status()
    result = response.json()
    if result.get('data') and len(result['data']) > 0:
        return result['data'][0]
    return None


def compute_stale_gap(last_heartbeat_str):
    try:
        if last_heartbeat_str:
            last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            gap_seconds = (now - last_heartbeat).total_seconds()
            return gap_seconds
    except Exception as e:
        logger.warning(f"Failed to parse heartbeat '{last_heartbeat_str}': {e}")
    return None


def tail_log_file(filepath, max_lines=100):
    errors = []
    exceptions = []
    if not os.path.exists(filepath):
        logger.warning(f"Log file not found: {filepath}")
        return errors, exceptions
    
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
            recent_lines = lines[-max_lines:] if len(lines) > max_lines else lines
            for line in recent_lines:
                if 'ERROR' in line:
                    errors.append(line.strip())
                elif 'EXCEPTION' in line:
                    exceptions.append(line.strip())
    except Exception as e:
        logger.error(f"Failed to read log file {filepath}: {e}")
    
    return errors, exceptions


def write_diagnostic_blob(diagnostic_data):
    payload = {
        "table": "mcp_daemon_diagnostics",
        "rows": diagnostic_data,
        "wait": True
    }
    response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
    response.raise_for_status()
    logger.info(f"Diagnostic blob written for {SERVICE_NAME}")


def run():
    logger.info(f"Starting diagnostic daemon for stale service: {SERVICE_NAME}")
    
    health = get_service_health()
    if not health:
        logger.error(f"No service_health record found for {SERVICE_NAME}")
        diagnostic = {
            "service": SERVICE_NAME,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "NOT_FOUND",
            "error": f"No service_health record for {SERVICE_NAME}",
            "stale_gap_seconds": None,
            "threshold_seconds": STALE_THRESHOLD_SECONDS,
            "is_stale": True,
            "recent_errors": [],
            "recent_exceptions": [],
            "diagnostic_type": "STALE_SERVICE"
        }
        write_diagnostic_blob(diagnostic)
        return
    
    last_heartbeat = health.get('last_heartbeat')
    stale_gap = compute_stale_gap(last_heartbeat)
    is_stale = stale_gap is not None and stale_gap > STALE_THRESHOLD_SECONDS
    
    recent_errors, recent_exceptions = tail_log_file(LOG_FILE)
    
    diagnostic = {
        "service": SERVICE_NAME,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "STALE" if is_stale else "HEALTHY",
        "last_heartbeat": last_heartbeat,
        "stale_gap_seconds": stale_gap,
        "threshold_seconds": STALE_THRESHOLD_SECONDS,
        "is_stale": is_stale,
        "recent_errors": recent_errors[-20:] if recent_errors else [],
        "recent_exceptions": recent_exceptions[-20:] if recent_exceptions else [],
        "log_file": LOG_FILE,
        "diagnostic_type": "STALE_SERVICE"
    }
    
    if is_stale:
        logger.warning(f"{SERVICE_NAME} is stale: gap={stale_gap}s, threshold={STALE_THRESHOLD_SECONDS}s")
    else:
        logger.info(f"{SERVICE_NAME} is healthy: gap={stale_gap}s")
    
    write_diagnostic_blob(diagnostic)


if __name__ == '__main__':
    run()