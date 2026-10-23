import requests
import logging
import subprocess
import psutil
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
HEALTH_THRESHOLD_SECONDS = 300

def check_http_connectivity():
    logger.info("=== CHECK 1: HTTP Connectivity to write_service:8772 ===")
    try:
        r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': 'SELECT 1'}, timeout=5)
        if r.status_code == 200:
            logger.info(f"✓ write_service responds: {r.status_code}")
            return True
        else:
            logger.warning(f"✗ write_service returns status: {r.status_code}")
            return False
    except requests.exceptions.ConnectionError as e:
        logger.error(f"✗ Cannot connect to write_service: {e}")
        return False
    except Exception as e:
        logger.error(f"✗ Unexpected error: {e}")
        return False

def check_heartbeat_entries():
    logger.info("=== CHECK 2: Heartbeat Table Entries for write_service ===")
    try:
        r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': "SELECT * FROM service_health WHERE service='write_service' ORDER BY last_heartbeat DESC LIMIT 5"}, timeout=8)
        if r.status_code == 200:
            entries = r.json().get('rows', [])
            if entries:
                for entry in entries:
                    ts = entry.get('last_heartbeat', '')
                    logger.info(f"  Heartbeat entry: {entry}")
                latest = entries[0].get('last_heartbeat', '')
                if latest:
                    try:
                        dt = datetime.fromisoformat(latest.replace('Z', '+00:00'))
                        age = (datetime.now(timezone.utc) - dt).total_seconds()
                        logger.info(f"  Latest heartbeat age: {age:.1f}s (threshold: {HEALTH_THRESHOLD_SECONDS}s)")
                        if age > HEALTH_THRESHOLD_SECONDS:
                            logger.warning(f"  ⚠ STALE: heartbeat is {age:.1f}s old")
                        else:
                            logger.info(f"  ✓ Healthy: heartbeat is recent")
                    except:
                        logger.warning(f"  Could not parse timestamp: {latest}")
            else:
                logger.warning("  ⚠ No heartbeat entries for write_service found")
        else:
            logger.error(f"  Query failed: {r.status_code}")
    except Exception as e:
        logger.error(f"  Error querying heartbeat: {e}")

def check_service_process():
    logger.info("=== CHECK 3: Service Process Status ===")
    try:
        result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if 'write_service' in line.lower() and 'grep' not in line:
                logger.info(f"  Process found: {line.strip()}")
                return True
        logger.warning("  ⚠ No write_service process found in ps output")
        return False
    except Exception as e:
        logger.error(f"  Error checking process: {e}")
    return False

def check_duckdb_locks():
    logger.info("=== CHECK 4: DuckDB Lock Analysis ===")
    try:
        r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': "SELECT * FROM duckdb_queries() LIMIT 10"}, timeout=8)
        if r.status_code == 200:
            queries = r.json().get('rows', [])
            if queries:
                logger.info(f"  Found {len(queries)} active queries")
                for q in queries:
                    logger.info(f"    Query: {q}")
            else:
                logger.info("  No active queries")
        else:
            logger.info("  duckdb_queries() function not available, trying alternative")
            
        r2 = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': "SELECT database, active_transactions, active_locks FROM duckdb_databases()"}, timeout=8)
        if r2.status_code == 200:
            locks = r2.json().get('rows', [])
            for lock in locks:
                logger.info(f"  DB Lock info: {lock}")
    except Exception as e:
        logger.error(f"  Error checking locks: {e}")

def check_write_errors():
    logger.info("=== CHECK 5: Recent Write Errors ===")
    try:
        r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': "SELECT * FROM write_errors ORDER BY timestamp DESC LIMIT 10"}, timeout=8)
        if r.status_code == 200:
            errors = r.json().get('rows', [])
            if errors:
                for err in errors:
                    logger.warning(f"  Write error: {err}")
            else:
                logger.info("  No recent write errors")
        else:
            logger.info("  write_errors table not available")
    except Exception as e:
        logger.error(f"  Error checking writes: {e}")

def check_system_resources():
    logger.info("=== CHECK 6: System Resources ===")
    try:
        cpu = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        logger.info(f"  CPU: {cpu}%")
        logger.info(f"  Memory: {mem.percent}% used ({mem.available / (1024**3):.1f}GB available)")
        logger.info(f"  Disk: {disk.percent}% used")
    except Exception as e:
        logger.error(f"  Error checking resources: {e}")

def diagnose():
    logger.info("=" * 60)
    logger.info("ZO-SENTINEL: write_service Stale Diagnostic")
    logger.info("=" * 60)
    
    check_http_connectivity()
    check_heartbeat_entries()
    check_service_process()
    check_duckdb_locks()
    check_write_errors()
    check_system_resources()
    
    logger.info("=" * 60)
    logger.info("Diagnostic complete")
    logger.info("If write_service is unreachable, check: (1) service process, (2) port binding, (3) firewall")
    logger.info("If heartbeat is stale but reachable, check: (1) service internal health, (2) database locks")
    logger.info("=" * 60)

if __name__ == '__main__':
    diagnose()