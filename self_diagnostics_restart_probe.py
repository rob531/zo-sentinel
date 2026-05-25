import os
import time
import signal
import requests
import logging
from datetime import datetime, timezone

SERVICE_NAME = 'self_diagnostics_restart_probe'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
PID_FILE_DIR = '/tmp'
HEARTBEAT_THRESHOLD_SECONDS = 600
PROTECTED_SERVICES = ['write_service', 'rug_pull_monitor']

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/self_diagnostics_restart_probe.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str) -> dict:
    """Query write_service via HTTP - SELECT only."""
    response = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json={'sql': sql},
        timeout=10
    )
    response.raise_for_status()
    return response.json()


def ws_write(table: str, rows: list) -> dict:
    """Write to write_service via HTTP."""
    response = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json={'table': table, 'rows': rows, 'wait': True},
        timeout=10
    )
    response.raise_for_status()
    return response.json()


def probe_pid_file(pid_file_path: str) -> dict:
    """Probe PID file for process health."""
    result = {
        'pid_file_exists': False,
        'pid': None,
        'process_alive': False,
        'pid_file_mtime': None,
        'pid_file_age_seconds': None
    }

    if not os.path.exists(pid_file_path):
        logger.warning(f"PID file not found: {pid_file_path}")
        return result

    result['pid_file_exists'] = True
    result['pid_file_mtime'] = os.path.getmtime(pid_file_path)
    result['pid_file_age_seconds'] = time.time() - result['pid_file_mtime']

    try:
        with open(pid_file_path, 'r') as f:
            pid_str = f.read().strip()
            result['pid'] = int(pid_str)

        if result['pid']:
            try:
                os.kill(result['pid'], 0)
                result['process_alive'] = True
                logger.info(f"Process {result['pid']} is alive (signal 0 succeeded)")
            except OSError as e:
                result['process_alive'] = False
                logger.warning(f"Process {result['pid']} is NOT alive: {e}")

    except (ValueError, IOError) as e:
        logger.error(f"Error reading/parsing PID file: {e}")

    return result


def probe_heartbeat(service_name: str) -> dict:
    """Check service heartbeat from service_health table via write_service."""
    result = {
        'found': False,
        'last_heartbeat': None,
        'age_seconds': None,
        'stale': None
    }

    sql = f"SELECT last_heartbeat FROM service_health WHERE service = '{service_name}'"

    try:
        resp = ws_query(sql)
        if resp and 'rows' in resp and len(resp['rows']) > 0:
            result['found'] = True
            last_hb = resp['rows'][0].get('last_heartbeat')

            if last_hb:
                result['last_heartbeat'] = last_hb

                if isinstance(last_hb, str):
                    try:
                        if last_hb.endswith('Z'):
                            last_hb = last_hb[:-1] + '+00:00'
                        heartbeat_time = datetime.fromisoformat(last_hb)
                    except ValueError:
                        heartbeat_time = datetime.fromtimestamp(float(last_hb), tz=timezone.utc)
                else:
                    heartbeat_time = last_hb

                age = (datetime.now(timezone.utc) - heartbeat_time).total_seconds()
                result['age_seconds'] = round(age, 1)
                result['stale'] = age > HEARTBEAT_THRESHOLD_SECONDS
                logger.info(f"Heartbeat age for {service_name}: {result['age_seconds']}s (stale={result['stale']})")

    except Exception as e:
        logger.error(f"Error querying heartbeat for {service_name}: {e}")

    return result


def probe_write_service_connectivity() -> dict:
    """Verify write_service is responsive via health endpoint."""
    result = {
        'reachable': False,
        'latency_ms': None,
        'error': None
    }

    start = time.time()
    try:
        resp = requests.get(
            f'{WRITE_SERVICE_URL}/health',
            timeout=5
        )
        result['reachable'] = resp.status_code == 200
        result['latency_ms'] = round((time.time() - start) * 1000, 2)
        if resp.status_code != 200:
            result['error'] = f"HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        result['error'] = 'timeout'
    except requests.exceptions.ConnectionError:
        result['error'] = 'connection_refused'
    except Exception as e:
        result['error'] = str(e)

    return result


def send_diagnostic_heartbeat():
    """Send our own heartbeat so this probe is trackable."""
    try:
        ws_write('service_health', [{
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'running',
            'meta': 'diagnostic probe for self_diagnostics staleness'
        }])
    except Exception as e:
        logger.warning(f"Failed to send own heartbeat: {e}")


def run():
    """Run diagnostic probe for self_diagnostics staleness."""
    logger.info("=" * 60)
    logger.info("SELF_DIAGNOSTICS STALENESS PROBE - STARTING")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info(f"Heartbeat threshold: {HEARTBEAT_THRESHOLD_SECONDS}s")
    logger.info("=" * 60)

    target_service = 'self_diagnostics'
    pid_file = os.path.join(PID_FILE_DIR, f'{target_service}.pid')

    logger.info(f"Probe target: {target_service}")
    logger.info(f"PID file path: {pid_file}")

    logger.info("\n--- PID FILE PROBE ---")
    if target_service in PROTECTED_SERVICES:
        logger.warning(f"[PROTECTED] {target_service} is in protected services list - skipping PID probe")
        pid_result = {'skipped': True, 'reason': 'protected_service'}
    else:
        pid_result = probe_pid_file(pid_file)
        logger.info(f"PID file exists: {pid_result['pid_file_exists']}")
        logger.info(f"PID value: {pid_result['pid']}")
        logger.info(f"Process alive: {pid_result['process_alive']}")
        logger.info(f"PID file age: {pid_result.get('pid_file_age_seconds', 'N/A')}s")

    logger.info("\n--- HEARTBEAT PROBE ---")
    hb_result = probe_heartbeat(target_service)
    logger.info(f"Found in service_health: {hb_result['found']}")
    logger.info(f"Last heartbeat: {hb_result.get('last_heartbeat', 'N/A')}")
    logger.info(f"Age seconds: {hb_result.get('age_seconds', 'N/A')}")
    logger.info(f"Marked stale: {hb_result.get('stale', 'N/A')}")

    logger.info("\n--- WRITE_SERVICE CONNECTIVITY ---")
    ws_result = probe_write_service_connectivity()
    logger.info(f"Reachable: {ws_result['reachable']}")
    logger.info(f"Latency ms: {ws_result.get('latency_ms', 'N/A')}")
    logger.info(f"Error: {ws_result.get('error', 'none')}")

    logger.info("\n--- DIAGNOSTIC SUMMARY ---")
    print(f"\n{'='*50}")
    print(f"  TARGET: {target_service}")
    print(f"  Protected: {target_service in PROTECTED_SERVICES}")
    print(f"  PID file: {pid_file}")
    if pid_result.get('skipped'):
        print(f"  PID probe: SKIPPED (protected)")
    else:
        print(f"  PID probe: process_alive={pid_result['process_alive']}, pid={pid_result.get('pid')}")
    print(f"  Heartbeat found: {hb_result['found']}")
    print(f"  Heartbeat age: {hb_result.get('age_seconds', 'N/A')}s")
    print(f"  Stale (>{HEARTBEAT_THRESHOLD_SECONDS}s): {hb_result.get('stale')}")
    print(f"  WriteService reachable: {ws_result['reachable']}")
    print(f"  WriteService latency: {ws_result.get('latency_ms', 'N/A')}ms")
    print(f"{'='*50}\n")

    if ws_result['reachable']:
        send_diagnostic_heartbeat()

    if not ws_result['reachable']:
        logger.error("CRITICAL: WriteService is unreachable - diagnostic incomplete")
    elif hb_result['stale'] and not pid_result.get('process_alive', False):
        logger.warning("STALE: self_diagnostics is stale and process appears dead")
    elif hb_result['stale'] and pid_result.get('process_alive', False):
        logger.warning("STALE: self_diagnostics heartbeat is stale but process is alive")
    else:
        logger.info("OK: No staleness detected")

    logger.info("=" * 60)
    logger.info("SELF_DIAGNOSTICS STALENESS PROBE - COMPLETE")
    logger.info("=" * 60)


if __name__ == '__main__':
    run()