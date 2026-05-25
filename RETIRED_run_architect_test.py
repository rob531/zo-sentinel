#!/usr/bin/env python3
"""
RETIRED_run_architect_test.py - Architecture integration test (RETIRED)
This file is retired - see tests/integration_test.py for active tests
"""
import os
import sys
import time
import requests
import logging
from typing import Dict, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE", "http://127.0.0.1:8772/write")
EXECUTE_URL = os.environ.get("EXECUTE_URL", "http://127.0.0.1:8773/execute")
QUERY_URL = os.environ.get("QUERY_URL", "http://127.0.0.1:8773/query")
SERVICE_NAME = "architect_test"

def get_write_url() -> str:
    return WRITE_SERVICE_URL

def get_execute_url() -> str:
    return EXECUTE_URL

def get_query_url() -> str:
    return QUERY_URL

def get_db_path() -> str:
    return os.environ.get("SENTINEL_DB_PATH", "/tmp/sentinel.duckdb")

def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Query via inference_router"""
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": params or {}},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return {"error": str(e)}

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service using 'rows' field - NOT 'row'"""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return False

def ws_execute(sql: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute via inference_router"""
    try:
        response = requests.post(
            EXECUTE_URL,
            json={"sql": sql, "params": params or {}},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Execute failed: {e}")
        return {"error": str(e)}

def check_single_instance() -> bool:
    """Ensure only one instance runs"""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        if os.path.exists(f"/proc/{old_pid}"):
            logger.warning(f"Another instance running with PID {old_pid}")
            return False
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True

def send_heartbeat() -> bool:
    """Send heartbeat to write_service"""
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    })

def test_write_service() -> bool:
    """Test write_service connectivity"""
    logger.info("Testing write_service...")
    try:
        response = requests.get(WRITE_SERVICE_URL.replace("/write", "/health"), timeout=5)
        if response.status_code == 200:
            logger.info("✓ write_service is healthy")
            return True
    except Exception:
        pass
    # Try direct write test
    return ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ")})

def test_inference_router() -> bool:
    """Test inference_router connectivity"""
    logger.info("Testing inference_router...")
    try:
        result = ws_query("SELECT 1 as test")
        if "error" not in result and not isinstance(result, dict) or (isinstance(result, dict) and "data" in result):
            logger.info("✓ inference_router query works")
            return True
    except Exception as e:
        logger.error(f"inference_router test failed: {e}")
    return False

def test_schema_access() -> bool:
    """Test if core tables exist"""
    logger.info("Testing schema access...")
    tables_to_check = ["mcp_registry", "mesh_events", "service_health"]
    for table in tables_to_check:
        result = ws_query(f"SELECT COUNT(*) FROM {table} LIMIT 1")
        if "error" not in str(result):
            logger.info(f"✓ Table {table} accessible")
        else:
            logger.warning(f"✗ Table {table} not accessible")
    return True

def run_tests() -> Dict[str, Any]:
    """Run all architecture tests"""
    results = {
        "write_service": test_write_service(),
        "inference_router": test_inference_router(),
        "schema_access": test_schema_access(),
        "heartbeat": send_heartbeat()
    }
    return results

def run():
    """Main run function"""
    logger.info("Starting architecture test...")
    if not check_single_instance():
        logger.error("Cannot acquire lock - another instance running")
        return
    
    try:
        results = run_tests()
        logger.info(f"Test results: {results}")
        
        # Write results
        ws_write("architect_test_results", {
            "test_name": SERVICE_NAME,
            "results": str(results),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")
        })
    finally:
        # Cleanup
        pid_file = f"/tmp/{SERVICE_NAME}.pid"
        if os.path.exists(pid_file):
            os.remove(pid_file)
    
    logger.info("Architecture test complete")

if __name__ == "__main__":
    run()