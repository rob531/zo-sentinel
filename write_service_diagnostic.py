import logging
import os
import signal
import sys
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = 'write_service_diagnostic'
SERVICE_PORT = None
PID_FILE = f'/home/workspace/run/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'

LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f'{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(SERVICE_NAME)

_write_pid_file = None

def check_single_instance():
    global _write_pid_file
    pid_file = Path(PID_FILE)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance running with PID {old_pid}. Exiting.")
            sys.exit(1)
        except OSError:
            logger.warning(f"Stale PID file found for {old_pid}. Overwriting.")
    pid_file.write_text(str(os.getpid()))
    _write_pid_file = pid_file

def remove_pid_file():
    if _write_pid_file and _write_pid_file.exists():
        _write_pid_file.unlink()

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}. Shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)

def ws_query(sql, params=None):
    """Query write_service and return result."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json=payload,
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return None

def ws_write(table, rows):
    """Write to write_service."""
    if isinstance(rows, dict):
        rows = [rows]
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return None

def check_write_service_health():
    """Ping write_service health endpoint."""
    try:
        resp = requests.get(
            f'{WRITE_SERVICE_URL}/health',
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Health check OK: {data}")
            return {'ok': True, 'data': data}
        else:
            logger.warning(f"Health endpoint returned status {resp.status_code}")
            return {'ok': False, 'status': resp.status_code}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {'ok': False, 'error': str(e)}

def check_service_responsiveness():
    """Check if write_service responds to query requests."""
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json={'sql': 'SELECT 1 as test'},
            timeout=15
        )
        if resp.status_code == 200:
            logger.info("Query request successful")
            return {'ok': True, 'response': resp.json()}
        else:
            logger.warning(f"Query request returned {resp.status_code}")
            return {'ok': False, 'status': resp.status_code}
    except Exception as e:
        logger.error(f"Query request failed: {e}")
        return {'ok': False, 'error': str(e)}

def check_heartbeat_status():
    """Check if heartbeat records exist and are recent."""
    result = {'written_by_service': False, 'written_by_callers': False, 'recent_entries': []}
    
    try:
        rows = ws_query("""
            SELECT service_name, status, last_heartbeat, meta
            FROM service_health
            ORDER BY last_heartbeat DESC
            LIMIT 20
        """)
        if rows and 'rows' in rows:
            result['recent_entries'] = rows['rows']
            logger.info(f"Found {len(rows['rows'])} recent heartbeat entries")
            
            now = datetime.now(timezone.utc)
            for entry in rows['rows']:
                hb_ts = entry.get('last_heartbeat', '')
                service = entry.get('service_name', '')
                
                if service == 'write_service':
                    result['written_by_service'] = True
                
                if 'write_service_diagnostic' in service:
                    result['written_by_callers'] = True
                    
                if hb_ts:
                    try:
                        if 'Z' in hb_ts:
                            hb_dt = datetime.fromisoformat(hb_ts.replace('Z', '+00:00'))
                            age_seconds = (now - hb_dt).total_seconds()
                            entry['age_seconds'] = age_seconds
                    except:
                        pass
        else:
            logger.warning("No heartbeat entries found")
    except Exception as e:
        logger.error(f"Heartbeat check failed: {e}")
    
    return result

def get_recent_writers():
    """Identify which callers last successfully wrote."""
    result = []
    try:
        rows = ws_query("""
            SELECT * FROM service_health
            ORDER BY last_heartbeat DESC
            LIMIT 50
        """)
        if rows and 'rows' in rows:
            for entry in rows['rows']:
                ts = entry.get('last_heartbeat', '')
                service = entry.get('service_name', '')
                status = entry.get('status', '')
                meta = entry.get('meta', '{}')
                
                if ts:
                    try:
                        if 'Z' in ts:
                            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                            age = (datetime.now(timezone.utc) - dt).total_seconds()
                            result.append({
                                'service': service,
                                'status': status,
                                'age_seconds': age,
                                'ts': ts,
                                'meta': meta
                            })
                    except:
                        pass
    except Exception as e:
        logger.error(f"Failed to get recent writers: {e}")
    return result

def diagnose():
    """Run full diagnostic and return report."""
    report = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'health_check': None,
        'responsiveness_check': None,
        'heartbeat_status': None,
        'recent_writers': [],
        'summary': {}
    }
    
    logger.info("Starting write_service diagnostic...")
    
    report['health_check'] = check_write_service_health()
    report['responsiveness_check'] = check_service_responsiveness()
    report['heartbeat_status'] = check_heartbeat_status()
    report['recent_writers'] = get_recent_writers()
    
    if report['health_check']['ok']:
        report['summary']['write_service_reachable'] = True
    else:
        report['summary']['write_service_reachable'] = False
        report['summary']['diagnosis'] = 'write_service is not responding to health checks'
    
    if report['responsiveness_check']['ok']:
        report['summary']['write_service_responsive'] = True
    else:
        report['summary']['write_service_responsive'] = False
        report['summary']['diagnosis'] = 'write_service is not responding to queries'
    
    hb = report['heartbeat_status']
    if hb['written_by_service']:
        report['summary']['heartbeat_source'] = 'written by write_service itself'
        report['summary']['diagnosis'] = 'write_service is writing its own heartbeat'
    elif hb['written_by_callers']:
        report['summary']['heartbeat_source'] = 'written by caller daemons'
        report['summary']['diagnosis'] = 'write_service heartbeat is being dropped; callers are writing their own'
    else:
        report['summary']['heartbeat_source'] = 'none detected'
        report['summary']['diagnosis'] = 'no heartbeat being written at all'
    
    if report['recent_writers']:
        fresh_count = sum(1 for w in report['recent_writers'] if w.get('age_seconds', 9999) < 300)
        report['summary']['fresh_writers_last_5min'] = fresh_count
        if fresh_count > 0:
            report['summary']['diagnosis'] += f'; {fresh_count} daemons writing heartbeat recently'
    
    logger.info(f"Diagnostic summary: {report['summary']}")
    
    return report

def send_heartbeat(status='running', meta=None):
    """Send diagnostic heartbeat to service_health."""
    if meta is None:
        meta = {}
    meta['last_cycle'] = datetime.now(timezone.utc).isoformat()
    
    rows = [{
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': str(meta)
    }]
    ws_write('service_health', rows)

def log_diagnostic_report(report):
    """Log diagnostic report details."""
    logger.info("=" * 60)
    logger.info("WRITE_SERVICE DIAGNOSTIC REPORT")
    logger.info("=" * 60)
    logger.info(f"Timestamp: {report['ts']}")
    logger.info(f"Summary: {report['summary']}")
    logger.info("-" * 60)
    
    hc = report.get('health_check', {})
    logger.info(f"Health Check: OK={hc.get('ok')}")
    if hc.get('data'):
        logger.info(f"  Response data: {hc['data']}")
    if hc.get('error'):
        logger.info(f"  Error: {hc['error']}")
    
    rc = report.get('responsiveness_check', {})
    logger.info(f"Responsiveness Check: OK={rc.get('ok')}")
    
    hb = report.get('heartbeat_status', {})
    logger.info(f"Heartbeat - written_by_service: {hb.get('written_by_service')}")
    logger.info(f"Heartbeat - written_by_callers: {hb.get('written_by_callers')}")
    
    logger.info("-" * 60)
    writers = report.get('recent_writers', [])
    if writers:
        logger.info(f"Recent writers ({len(writers)} entries):")
        for w in writers[:10]:
            logger.info(f"  {w['service']} - age={w.get('age_seconds', 'unknown')}s - status={w.get('status')}")
    logger.info("=" * 60)

def cycle():
    """Run one diagnostic cycle."""
    logger.info("Running diagnostic cycle...")
    
    report = diagnose()
    log_diagnostic_report(report)
    
    ws_write('diagnostic_reports', {
        'report_type': 'write_service_diagnostic',
        'ts': report['ts'],
        'summary': str(report['summary']),
        'health_ok': report['health_check'].get('ok', False),
        'responsive_ok': report['responsiveness_check'].get('ok', False),
        'heartbeat_written_by_service': report['heartbeat_status'].get('written_by_service', False),
        'heartbeat_written_by_callers': report['heartbeat_status'].get('written_by_callers', False),
        'fresh_writers': len([w for w in report['recent_writers'] if w.get('age_seconds', 9999) < 300]),
        'full_report': str(report)
    })
    
    send_heartbeat(status='completed', meta=report['summary'])
    logger.info("Diagnostic cycle complete")

def run():
    """Main daemon loop."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} starting...")
    logger.info(f"Target: {WRITE_SERVICE_URL}")
    
    POLL_SECS = 300
    
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
            send_heartbeat(status='error', meta={'error': str(e)})
        
        logger.info(f"Sleeping {POLL_SECS}s before next cycle...")
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()