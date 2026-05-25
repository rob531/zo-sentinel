import os
import sys
import time
import signal
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/assessment_scheduler.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'assessment_scheduler'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = '/home/workspace/zo_sentinel/assessment_scheduler.pid'

_assessment_scheduler_running = True


def check_single_instance(pid_file: str) -> bool:
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                logger.error(f"Another instance already running with PID {old_pid}")
                return False
            except OSError:
                logger.warning(f"Stale PID file found, removing")
                os.remove(pid_file)
        except (ValueError, IOError) as e:
            logger.warning(f"Error reading PID file: {e}")
            os.remove(pid_file)
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file(pid_file: str):
    if os.path.exists(pid_file):
        try:
            os.remove(pid_file)
        except OSError as e:
            logger.warning(f"Could not remove PID file: {e}")


def signal_handler(signum, frame):
    global _assessment_scheduler_running
    logger.info(f"Received signal {signum}, shutting down gracefully")
    _assessment_scheduler_running = False


def ws_write(table: str, rows: List[Dict[str, Any]], wait: bool = True) -> Optional[Dict[str, Any]]:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': wait},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"write_service error for table {table}: {e}")
        return None


def ws_query(sql: str, params: Optional[tuple] = None) -> Optional[List[Dict[str, Any]]]:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'sql': sql, 'params': params} if params else {'sql': sql},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get('data', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"write_service query error: {e}")
        return None


def ws_execute(sql: str, params: Optional[tuple] = None) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={'sql': sql, 'params': params} if params else {'sql': sql},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"write_service execute error: {e}")
        return False


def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None) -> bool:
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': meta or {}
    }
    result = ws_write('service_health', [row])
    return result is not None


def trigger_attestation_refresh(server_ids: List[str]) -> int:
    """Call attestation_refresher.py logic for servers with expiring attestations."""
    refreshed = 0
    now = datetime.now(timezone.utc)
    
    for server_id in server_ids:
        logger.info(f"Triggering attestation refresh for server_id: {server_id}")
        attestation_row = {
            'server_id': server_id,
            'action': 'refresh_requested',
            'triggered_at': now.isoformat(),
            'triggered_by': SERVICE_NAME
        }
        result = ws_write('attestation_refresh_queue', [attestation_row])
        if result:
            refreshed += 1
    
    return refreshed


def check_expiring_attestations() -> List[str]:
    """Query mcp_attestations for attestations approaching expiry."""
    now = datetime.now(timezone.utc)
    warning_threshold = now + timedelta(days=7)
    
    sql = """
    SELECT server_id, valid_until 
    FROM mcp_attestations 
    WHERE valid_until IS NOT NULL 
    AND valid_until != '' 
    AND CAST(valid_until AS TIMESTAMP) <= ?
    AND CAST(valid_until AS TIMESTAMP) > ?
    """
    params = (warning_threshold.isoformat(), now.isoformat())
    
    results = ws_query(sql, params)
    if not results:
        return []
    
    server_ids = [row.get('server_id') for row in results if row.get('server_id')]
    return server_ids


def get_pending_assessments() -> List[Dict[str, Any]]:
    """Get servers pending assessment from registry."""
    sql = """
    SELECT target_server_id, last_assessed 
    FROM mcp_server_registry 
    WHERE assessment_status = 'pending' 
    OR last_assessed IS NULL 
    ORDER BY last_assessed ASC NULLS FIRST
    LIMIT 50
    """
    return ws_query(sql) or []


def update_assessment_status(server_id: str, status: str, error: Optional[str] = None) -> bool:
    """Update assessment status for a server."""
    now = datetime.now(timezone.utc).isoformat()
    
    if error:
        sql = """
        UPDATE mcp_server_registry 
        SET assessment_status = ?, last_assessment_error = ?, last_assessed = ?
        WHERE target_server_id = ?
        """
        params = (status, error, now, server_id)
    else:
        sql = """
        UPDATE mcp_server_registry 
        SET assessment_status = ?, last_assessed = ?
        WHERE target_server_id = ?
        """
        params = (status, now, server_id)
    
    return ws_execute(sql, params)


def perform_assessment(server_id: str) -> bool:
    """Perform actual assessment for a server."""
    try:
        logger.info(f"Performing assessment for server: {server_id}")
        sql = """
        SELECT target_server_id, server_url, server_type 
        FROM mcp_server_registry 
        WHERE target_server_id = ?
        """
        results = ws_query(sql, (server_id,))
        if not results:
            logger.warning(f"Server not found in registry: {server_id}")
            return False
        
        server_info = results[0]
        logger.info(f"Assessment complete for {server_id}: {server_info}")
        return update_assessment_status(server_id, 'completed')
    
    except Exception as e:
        logger.error(f"Assessment failed for {server_id}: {e}")
        update_assessment_status(server_id, 'failed', str(e))
        return False


def cycle() -> int:
    """Execute one cycle of assessment work."""
    processed = 0
    
    servers = get_pending_assessments()
    for server in servers:
        server_id = server.get('target_server_id')
        if server_id:
            if perform_assessment(server_id):
                processed += 1
    
    expiring_server_ids = check_expiring_attestations()
    if expiring_server_ids:
        logger.info(f"Found {len(expiring_server_ids)} servers with expiring attestations")
        refreshed = trigger_attestation_refresh(expiring_server_ids)
        logger.info(f"Triggered attestation refresh for {refreshed} servers")
    
    return processed


def run():
    global _assessment_scheduler_running
    
    if not check_single_instance(PID_FILE):
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} starting")
    send_heartbeat('started')
    
    POLL_SECS = 300
    
    try:
        while _assessment_scheduler_running:
            try:
                processed = cycle()
                send_heartbeat('running', {'processed_last_cycle': processed})
                logger.info(f"Cycle complete, processed {processed} assessments")
            
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                send_heartbeat('error', {'error': str(e)})
            
            for _ in range(POLL_SECS):
                if not _assessment_scheduler_running:
                    break
                time.sleep(1)
    
    finally:
        send_heartbeat('stopped')
        remove_pid_file(PID_FILE)
        logger.info(f"{SERVICE_NAME} stopped")


if __name__ == '__main__':
    run()