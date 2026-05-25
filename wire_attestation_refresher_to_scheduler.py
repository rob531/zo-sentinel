import sys
import time
import signal
import logging
from datetime import datetime, timezone

sys.path.insert(0, '/home/workspace/zo_sentinel')

SERVICE_NAME = 'attestation_refresher_scheduler_wiring'
SERVICE_PORT = 8791
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
LOG_FILE = '/home/workspace/zo_sentinel/logs/attestation_refresher_wiring.log'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
POLL_SECS = 60
HEARTBEAT_INTERVAL = 30

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)


def ws_write(table, rows):
    import requests
    try:
        resp = requests.post(f'{WRITE_SERVICE_URL}/write', json={'table': table, 'rows': rows}, timeout=10)
        return resp.json()
    except Exception as e:
        log.error(f'ws_write error: {e}')
        return None


def ws_query(sql):
    import requests
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        log.error(f'ws_query error: {e}')
        return []


def ws_execute(sql):
    import requests
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={'sql': sql}, timeout=30)
        return resp.json()
    except Exception as e:
        log.error(f'ws_execute error: {e}')
        return None


def send_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': now})


def check_single_instance():
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f'{SERVICE_NAME} already running as PID {old_pid}')
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    import os
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    log.info(f'Received signal {signum}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


def verify_attestation_refresher():
    try:
        import attestation_refresher
        if hasattr(attestation_refresher, 'run'):
            log.info('attestation_refresher.run() verified')
            return True
        else:
            log.error('attestation_refresher.run() not found')
            return False
    except ImportError as e:
        log.error(f'Failed to import attestation_refresher: {e}')
        return False


def get_pending_attestation_refreshes():
    sql = """
    SELECT 
        server_id,
        mcp_name,
        next_refresh_due,
        created_at
    FROM mcp_attestations 
    WHERE next_refresh_due IS NOT NULL 
    AND next_refresh_due < NOW()
    ORDER BY next_refresh_due ASC
    LIMIT 100
    """
    return ws_query(sql)


def get_all_attestations_needing_refresh():
    sql = """
    SELECT 
        server_id,
        mcp_name,
        COALESCE(next_refresh_due, created_at) as due_time
    FROM mcp_attestations 
    WHERE next_refresh_due IS NOT NULL
    """
    return ws_query(sql)


def get_attestation_metrics():
    sql_total = "SELECT COUNT(*) as total FROM mcp_attestations"
    sql_refresh_due = "SELECT COUNT(*) as due FROM mcp_attestations WHERE next_refresh_due IS NOT NULL AND next_refresh_due < NOW()"
    sql_expired = """
    SELECT COUNT(*) as expired FROM mcp_attestations 
    WHERE expiry_date IS NOT NULL AND expiry_date < NOW()
    """
    
    result_total = ws_query(sql_total)
    result_due = ws_query(sql_refresh_due)
    result_expired = ws_query(sql_expired)
    
    return {
        'total': result_total[0]['total'] if result_total else 0,
        'refresh_due': result_due[0]['due'] if result_due else 0,
        'expired': result_expired[0]['expired'] if result_expired else 0
    }


def query_scheduler_events():
    sql = """
    SELECT 
        event_id,
        event_type,
        target_server_id,
        target_mcp_name,
        created_at,
        processed_at,
        status
    FROM mesh_events 
    WHERE event_type LIKE '%attestation%'
    ORDER BY created_at DESC
    LIMIT 50
    """
    return ws_query(sql)


def record_attestation_refresh_event(server_id, mcp_name, status, details):
    now = datetime.now(timezone.utc).isoformat()
    ws_write('mesh_events', {
        'event_id': f'attestation_refresh_{int(time.time())}',
        'event_type': 'attestation_refresh',
        'target_server_id': server_id,
        'target_mcp_name': mcp_name,
        'created_at': now,
        'processed_at': now if status == 'completed' else None,
        'status': status,
        'details': details
    })


def trigger_attestation_refresh(server_id, mcp_name):
    try:
        import attestation_refresher
        if hasattr(attestation_refresher, 'run'):
            log.info(f'Triggering attestation refresh for {mcp_name} (server_id={server_id})')
            result = attestation_refresher.run()
            record_attestation_refresh_event(
                server_id, 
                mcp_name, 
                'completed', 
                f'Attestation refresh triggered at {datetime.now(timezone.utc).isoformat()}'
            )
            return result
        return None
    except Exception as e:
        log.error(f'Error triggering refresh for {mcp_name}: {e}')
        record_attestation_refresh_event(
            server_id,
            mcp_name,
            'failed',
            f'Refresh failed: {str(e)}'
        )
        return None


def process_pending_refreshes():
    pending = get_pending_attestation_refreshes()
    log.info(f'Found {len(pending)} attestations due for refresh')
    
    processed = 0
    for attestation in pending:
        server_id = attestation.get('server_id')
        mcp_name = attestation.get('mcp_name')
        if server_id and mcp_name:
            result = trigger_attestation_refresh(server_id, mcp_name)
            if result:
                processed += 1
    
    return processed


def update_next_refresh_due(server_id, next_refresh_ts):
    sql = f"""
    UPDATE mcp_attestations 
    SET next_refresh_due = '{next_refresh_ts}'
    WHERE server_id = '{server_id}'
    """
    return ws_execute(sql)


def ensure_wiring_table():
    sql = """
    CREATE SEQUENCE IF NOT EXISTS wiring_events_id_seq
    """
    ws_execute(sql)
    
    sql = """
    CREATE TABLE IF NOT EXISTS wiring_attestation_scheduler (
        wiring_id BIGINT DEFAULT nextval('wiring_events_id_seq'),
        server_id VARCHAR(255),
        mcp_name VARCHAR(255),
        last_checked TIMESTAMP,
        last_refresh_triggered TIMESTAMP,
        refresh_count INTEGER DEFAULT 0,
        status VARCHAR(50),
        created_at TIMESTAMP DEFAULT NOW(),
        PRIMARY KEY (wiring_id)
    )
    """
    ws_execute(sql)


def log_wiring_event(server_id, mcp_name, event_type, status):
    now = datetime.now(timezone.utc).isoformat()
    sql = f"""
    INSERT INTO wiring_attestation_scheduler 
    (server_id, mcp_name, last_checked, status)
    VALUES ('{server_id}', '{mcp_name}', '{now}', '{status}')
    """
    return ws_execute(sql)


def get_scheduler_status():
    sql = "SELECT COUNT(*) as pending FROM mesh_events WHERE event_type = 'attestation_refresh' AND status = 'pending'"
    result = ws_query(sql)
    pending = result[0]['pending'] if result else 0
    
    sql_processed = "SELECT COUNT(*) as processed FROM mesh_events WHERE event_type = 'attestation_refresh' AND status = 'completed'"
    result_processed = ws_query(sql_processed)
    processed = result_processed[0]['processed'] if result_processed else 0
    
    return {
        'pending_refresh_events': pending,
        'completed_refresh_events': processed,
        'last_check': datetime.now(timezone.utc).isoformat()
    }


def run_cycle():
    log.info('Starting attestation refresher wiring cycle')
    
    try:
        verify_attestation_refresher()
        
        metrics = get_attestation_metrics()
        log.info(f'Attestation metrics: {metrics}')
        
        pending_count = process_pending_refreshes()
        log.info(f'Processed {pending_count} attestation refreshes')
        
        scheduler_status = get_scheduler_status()
        log.info(f'Scheduler status: {scheduler_status}')
        
        send_heartbeat()
        return True
    except Exception as e:
        log.error(f'Error in wiring cycle: {e}')
        return False


def heartbeat_loop():
    start_time = time.time()
    while True:
        try:
            send_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)
        except Exception as e:
            log.error(f'Heartbeat error: {e}')


def run():
    log.info(f'Starting {SERVICE_NAME}')
    
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_wiring_table()
    
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    log.info('Attestation refresher wiring service started')
    send_heartbeat()
    
    while True:
        try:
            run_cycle()
            time.sleep(POLL_SECS)
        except Exception as e:
            log.error(f'Main loop error: {e}')
            time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()