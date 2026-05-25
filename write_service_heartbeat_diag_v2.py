import logging
import sys
import requests
import time
from datetime import datetime, timezone

SERVICE_NAME = "write_service_heartbeat_diag_v2"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    return logging.getLogger(SERVICE_NAME)

logger = setup_logging()

def check_service_process():
    """Check if write_service process appears to be alive."""
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC 1: Service Process Status")
    logger.info("=" * 60)
    try:
        resp = requests.get(f"{WRITE_SERVICE_URL}/health", timeout=5)
        logger.info(f"  Result: PASS - Service responding with status {resp.status_code}")
        return True
    except requests.exceptions.ConnectionError:
        logger.warning("  Result: FAIL - Cannot connect to write_service at port 8772")
        return False
    except requests.exceptions.Timeout:
        logger.warning("  Result: TIMEOUT - Service at 8772 not responding")
        return False
    except Exception as e:
        logger.error(f"  Result: ERROR - {type(e).__name__}: {e}")
        return False

def check_import_chain():
    """Check if write_service can be reached and imports would succeed."""
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC 2: Import Chain Validation")
    logger.info("=" * 60)
    import_report = []
    
    required_modules = [
        ('logging', 'Logging subsystem'),
        ('requests', 'HTTP client'),
        ('fastapi', 'API framework'),
        ('uvicorn', 'ASGI server'),
        ('duckdb', 'DuckDB database'),
    ]
    
    all_ok = True
    for module, description in required_modules:
        try:
            __import__(module)
            logger.info(f"  [OK] {module}: {description}")
            import_report.append(f"  [OK] {module}: {description}")
        except ImportError as e:
            logger.error(f"  [FAIL] {module}: {description} - {e}")
            import_report.append(f"  [FAIL] {module}: {description} - {e}")
            all_ok = False
    
    if all_ok:
        logger.info("  Result: PASS - All required modules importable")
    else:
        logger.error("  Result: FAIL - One or more required modules missing")
    
    return all_ok, import_report

def check_heartbeat_table_reachable():
    """Check if service_health table is reachable via write_service query endpoint."""
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC 3: Heartbeat Table Reachability")
    logger.info("=" * 60)
    
    try:
        query_payload = {
            "sql": "SELECT COUNT(*) as cnt FROM service_health",
            "params": {}
        }
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=query_payload, timeout=10)
        
        if resp.status_code == 200:
            result = resp.json()
            logger.info(f"  Result: PASS - service_health table reachable")
            logger.info(f"  Row count: {result.get('data', [{}])[0].get('cnt', 'unknown')}")
            return True, result
        else:
            logger.error(f"  Result: FAIL - Query returned status {resp.status_code}")
            logger.error(f"  Response: {resp.text[:500]}")
            return False, None
            
    except requests.exceptions.ConnectionError:
        logger.error("  Result: FAIL - Cannot connect to /query endpoint")
        return False, None
    except requests.exceptions.Timeout:
        logger.error("  Result: TIMEOUT - /query endpoint not responding")
        return False, None
    except Exception as e:
        logger.error(f"  Result: ERROR - {type(e).__name__}: {e}")
        return False, None

def check_last_heartbeat_value():
    """Check the actual last_heartbeat value for write_service in service_health."""
    logger.info("=" * 60)
    logger.info("DIAGNOSTIC 4: write_service last_heartbeat Value")
    logger.info("=" * 60)
    
    try:
        query_payload = {
            "sql": "SELECT target_server_id, last_heartbeat FROM service_health WHERE target_server_id = 'write_service'",
            "params": {}
        }
        resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=query_payload, timeout=10)
        
        if resp.status_code == 200:
            result = resp.json()
            rows = result.get('data', [])
            
            if not rows:
                logger.warning("  Result: WARNING - No row found for 'write_service' in service_health")
                return None, None
            
            row = rows[0]
            target_server_id = row.get('target_server_id')
            last_heartbeat_str = row.get('last_heartbeat')
            
            logger.info(f"  target_server_id: {target_server_id}")
            logger.info(f"  last_heartbeat raw value: {last_heartbeat_str}")
            
            if last_heartbeat_str:
                try:
                    if isinstance(last_heartbeat_str, str):
                        last_heartbeat_dt = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
                    else:
                        last_heartbeat_dt = last_heartbeat_str
                    
                    now = datetime.now(timezone.utc)
                    age_seconds = (now - last_heartbeat_dt.replace(tzinfo=timezone.utc)).total_seconds()
                    age_hours = age_seconds / 3600
                    age_minutes = (age_seconds % 3600) / 60
                    
                    logger.info(f"  Heartbeat age: {int(age_hours)}h {int(age_minutes)}m ({age_seconds:.0f} seconds)")
                    
                    if age_seconds > 3600:
                        logger.warning(f"  Result: STALE - Heartbeat is {int(age_hours)}h {int(age_minutes)}m old (threshold: 1 hour)")
                    elif age_seconds > 300:
                        logger.warning(f"  Result: WARNING - Heartbeat is {int(age_minutes)}m old (threshold: 5 minutes)")
                    else:
                        logger.info(f"  Result: OK - Heartbeat is {int(age_seconds)}s old")
                    
                    return last_heartbeat_dt, age_seconds
                except Exception as e:
                    logger.error(f"  Result: ERROR - Could not parse last_heartbeat: {e}")
                    return None, None
            else:
                logger.error("  Result: FAIL - last_heartbeat is NULL")
                return None, None
        else:
            logger.error(f"  Result: FAIL - Query returned status {resp.status_code}")
            return None, None
            
    except Exception as e:
        logger.error(f"  Result: ERROR - {type(e).__name__}: {e}")
        return None, None

def generate_diagnostic_report():
    """Generate comprehensive diagnostic report."""
    logger.info("")
    logger.info("=" * 60)
    logger.info(f"ZO-SENTINEL DIAGNOSTIC REPORT: write_service heartbeat")
    logger.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)
    
    proc_ok = check_service_process()
    logger.info("")
    
    import_ok, import_report = check_import_chain()
    logger.info("")
    
    table_ok, table_result = check_heartbeat_table_reachable()
    logger.info("")
    
    heartbeat_dt, age_seconds = check_last_heartbeat_value()
    logger.info("")
    
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)
    logger.info(f"  Service Process Alive:      {'YES' if proc_ok else 'NO'}")
    logger.info(f"  Import Chain Complete:       {'YES' if import_ok else 'NO'}")
    logger.info(f"  Heartbeat Table Reachable:   {'YES' if table_ok else 'NO'}")
    
    if age_seconds is not None:
        logger.info(f"  Heartbeat Age:               {int(age_seconds/3600)}h {(age_seconds%3600)/60:.0f}m ({age_seconds:.0f}s)")
        if age_seconds > 3600:
            logger.info(f"  Heartbeat Status:            STALE (>1 hour)")
        elif age_seconds > 300:
            logger.info(f"  Heartbeat Status:            WARNING (>5 min)")
        else:
            logger.info(f"  Heartbeat Status:            OK")
    else:
        logger.info(f"  Heartbeat Age:               UNKNOWN")
        logger.info(f"  Heartbeat Status:            UNKNOWN")
    
    logger.info("")
    
    if proc_ok and table_ok and heartbeat_dt:
        if age_seconds and age_seconds > 3600:
            logger.info("DIAGNOSIS: write_service process is alive but heartbeat is stale.")
            logger.info("  Possible causes:")
            logger.info("    - Heartbeat sender task crashed or is blocked")
            logger.info("    - Write to service_health table failing silently")
            logger.info("    - Database write lock contention")
            logger.info("    - Clock sync issue between services")
        else:
            logger.info("DIAGNOSIS: write_service appears healthy.")
    elif not proc_ok:
        logger.info("DIAGNOSIS: write_service process is not responding at port 8772.")
    elif not table_ok:
        logger.info("DIAGNOSIS: Cannot reach service_health table via write_service.")
    elif not heartbeat_dt:
        logger.info("DIAGNOSIS: write_service row missing from service_health table.")
    
    logger.info("")
    logger.info("NOTE: This diagnostic script cannot restart or modify write_service.")
    logger.info("      Manual intervention required if service needs to be restarted.")
    logger.info("=" * 60)

def run():
    logger.info(f"Starting {SERVICE_NAME}")
    generate_diagnostic_report()

if __name__ == "__main__":
    run()