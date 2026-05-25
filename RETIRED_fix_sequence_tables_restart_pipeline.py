#!/usr/bin/env python3
"""
RETIRED_fix_sequence_tables_restart_pipeline.py
Fix sequence tables and restart the pipeline.
Uses write_service API exclusively - NO direct duckdb.connect()
"""

import os
import sys
import time
import signal
import logging
import requests
from typing import Optional, Dict, Any, List

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
SERVICE_NAME = "RETIRED_fix_sequence_tables_restart_pipeline"
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772/write")
QUERY_SERVICE_URL = os.environ.get("QUERY_SERVICE_URL", "http://127.0.0.1:8773/query")
EXECUTE_URL = os.environ.get("EXECUTE_URL", "http://127.0.0.1:8773/execute")
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
HEARTBEAT_INTERVAL = 30

# Tables that need sequence fixes
SEQUENCE_TABLES = [
    "mcp_servers",
    "assessment_events", 
    "mesh_events",
    "signal_scores",
    "approval_workflow",
    "trust_scores"
]


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_db_path() -> str:
    return os.environ.get("SENTINEL_DB_PATH", "/tmp/sentinel.duckdb")


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service API with 'rows' field."""
    url = get_write_url()
    payload = {"table": table, "rows": rows}
    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code in (200, 201):
            return True
        logger.warning(f"Write failed for {table}: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Write error for {table}: {e}")
        return False


def ws_query(sql: str, params: Optional[List] = None) -> Optional[List[Dict[str, Any]]]:
    """Query using query service API."""
    url = get_query_url()
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        response = requests.post(url, json=payload, timeout=60)
        if response.status_code == 200:
            data = response.json()
            return data.get("rows", [])
        logger.warning(f"Query failed: {response.status_code} - {response.text}")
        return None
    except Exception as e:
        logger.error(f"Query error: {e}")
        return None


def ws_execute(sql: str, params: Optional[List] = None) -> bool:
    """Execute SQL using execute service API."""
    url = get_execute_url()
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        response = requests.post(url, json=payload, timeout=60)
        if response.status_code in (200, 201):
            return True
        logger.warning(f"Execute failed: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Execute error: {e}")
        return False


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Check if process exists
            os.kill(old_pid, 0)
            logger.error(f"Another instance already running with PID {old_pid}")
            return False
        except (ValueError, ProcessLookupError, PermissionError):
            # Stale PID file, remove it
            os.remove(PID_FILE)
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Remove the PID file."""
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    """Send heartbeat to write_service."""
    from datetime import datetime
    rows = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat(),
        "status": "running"
    }
    ws_write("service_health", rows)


def get_tables_needing_sequences() -> List[str]:
    """Check which tables need sequence fixes."""
    tables_to_fix = []
    
    for table in SEQUENCE_TABLES:
        # Check if table exists and query current max id
        result = ws_query(f"SELECT MAX(id) as max_id FROM {table}")
        if result:
            logger.info(f"Table {table}: max_id = {result[0].get('max_id')}")
            tables_to_fix.append(table)
        else:
            logger.warning(f"Table {table} query failed or table doesn't exist")
    
    return tables_to_fix


def fix_sequences_for_table(table_name: str) -> bool:
    """Fix sequence for a specific table using execute API."""
    logger.info(f"Fixing sequence for table: {table_name}")
    
    # Get current max id
    result = ws_query(f"SELECT COALESCE(MAX(id), 0) as max_id FROM {table_name}")
    if not result:
        logger.error(f"Could not get max id for {table_name}")
        return False
    
    max_id = result[0].get('max_id', 0)
    logger.info(f"Current max_id for {table_name}: {max_id}")
    
    # Try to fix sequence using ALTER SEQUENCE
    fix_sql = f"ALTER TABLE {table_name} ALTER COLUMN id RESTART WITH {max_id + 1}"
    success = ws_execute(fix_sql)
    
    if success:
        logger.info(f"Successfully restarted sequence for {table_name}")
    else:
        logger.warning(f"Sequence fix for {table_name} may have failed, attempting alternative...")
        # Alternative: INSERT dummy and delete to advance sequence
        alt_sql = f"INSERT INTO {table_name} (id) VALUES ({max_id + 1})"
        if ws_execute(alt_sql):
            ws_execute(f"DELETE FROM {table_name} WHERE id = {max_id + 1}")
            logger.info(f"Alternative sequence fix worked for {table_name}")
            success = True
    
    return success


def ensure_service_health_table():
    """Ensure service_health table exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS service_health (
        service VARCHAR,
        last_heartbeat TIMESTAMP,
        status VARCHAR,
        PRIMARY KEY (service)
    )
    """
    ws_execute(sql)


def restart_pipeline_daemons():
    """Restart pipeline daemons via supervisord."""
    import subprocess
    
    daemons_to_restart = [
        "sentinel_signal_analyser",
        "sentinel_risk_ranker",
        "sentinel_mcp_scanner",
        "sentinel_attestation_engine"
    ]
    
    for daemon in daemons_to_restart:
        try:
            logger.info(f"Restarting {daemon}...")
            result = subprocess.run(
                ["supervisorctl", "restart", daemon],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                logger.info(f"Successfully restarted {daemon}")
            else:
                logger.warning(f"Failed to restart {daemon}: {result.stderr}")
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout restarting {daemon}")
        except FileNotFoundError:
            logger.warning("supervisorctl not found, skipping daemon restart")
            break
        except Exception as e:
            logger.error(f"Error restarting {daemon}: {e}")


def run():
    """Main execution function."""
    logger.info(f"Starting {SERVICE_NAME}")
    
    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Check single instance
    if not check_single_instance():
        logger.error("Cannot acquire lock, exiting")
        sys.exit(1)
    
    try:
        # Ensure required tables exist
        ensure_service_health_table()
        
        # Send initial heartbeat
        send_heartbeat()
        
        # Find tables needing sequence fixes
        tables_to_fix = get_tables_needing_sequences()
        
        # Fix sequences
        fixed_count = 0
        for table in tables_to_fix:
            if fix_sequences_for_table(table):
                fixed_count += 1
        
        logger.info(f"Fixed sequences for {fixed_count}/{len(tables_to_fix)} tables")
        
        # Restart pipeline daemons
        restart_pipeline_daemons()
        
        # Final heartbeat
        rows = {
            "service": SERVICE_NAME,
            "last_heartbeat": __import__('datetime').datetime.utcnow().isoformat(),
            "status": "completed",
            "tables_fixed": fixed_count
        }
        ws_write("service_health", rows)
        
        logger.info(f"{SERVICE_NAME} completed successfully")
        
    except Exception as e:
        logger.error(f"Fatal error in {SERVICE_NAME}: {e}")
        raise
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()