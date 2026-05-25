import json
import logging
import os
import signal
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import psutil
import requests

# Configuration
SERVICE_NAME = "mcp_scanner"
HEARTBEAT_THRESHOLD_SECONDS = 14400
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
LOG_PATH = Path("/var/log/zo_sentinel/mcp_scanner.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def check_process_alive() -> bool:
    """Check if mcp_scanner process is running via psutil."""
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                cmdline_str = " ".join(cmdline).lower()
                if "mcp_scanner" in cmdline_str or "zo_sentinel" in cmdline_str:
                    if "diagnose" not in cmdline_str:
                        logger.info(f"Found mcp_scanner process: PID={proc.info['pid']}")
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        logger.warning("mcp_scanner process not found running")
        return False
    except Exception as e:
        logger.error(f"Error checking process: {e}")
        return False


def get_last_log_entry() -> Optional[str]:
    """Get the last log entry timestamp."""
    try:
        if LOG_PATH.exists():
            with open(LOG_PATH, "r") as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    logger.info(f"Last log entry: {last_line[:100]}")
                    return last_line
        else:
            log_path_alt = Path("/home/workspace/zo_sentinel/logs/mcp_scanner.log")
            if log_path_alt.exists():
                with open(log_path_alt, "r") as f:
                    lines = f.readlines()
                    if lines:
                        last_line = lines[-1].strip()
                        logger.info(f"Last log entry (alt): {last_line[:100]}")
                        return last_line
        logger.warning("No log file found")
        return None
    except Exception as e:
        logger.error(f"Error reading log file: {e}")
        return None


def get_last_heartbeat() -> Optional[str]:
    """Query write_service for last heartbeat of mcp_scanner."""
    try:
        payload = {
            "table": "service_health",
            "query": {
                "filter": {"service": "mcp_scanner"},
                "order_by": [{"column": "last_heartbeat", "asc": False}],
                "limit": 1
            }
        }
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            rows = data.get("rows", [])
            if rows:
                last_hb = rows[0].get("last_heartbeat")
                logger.info(f"Last heartbeat from DB: {last_hb}")
                return last_hb
        return None
    except requests.exceptions.ConnectionError:
        logger.error("Cannot connect to write_service")
        return None
    except Exception as e:
        logger.error(f"Error querying heartbeat: {e}")
        return None


def get_queue_depth() -> int:
    """Check pending work in queue (file-based or write_service)."""
    try:
        queue_path = Path("/var/run/zo_sentinel/mcp_scanner_queue.json")
        if queue_path.exists():
            with open(queue_path, "r") as f:
                queue_data = json.load(f)
                depth = queue_data.get("depth", 0) if isinstance(queue_data, dict) else len(queue_data)
                logger.info(f"Queue depth from file: {depth}")
                return depth
        
        queue_path_alt = Path("/home/workspace/zo_sentinel/queue/mcp_scanner_queue.json")
        if queue_path_alt.exists():
            with open(queue_path_alt, "r") as f:
                queue_data = json.load(f)
                depth = queue_data.get("depth", 0) if isinstance(queue_data, dict) else len(queue_data)
                logger.info(f"Queue depth from alt file: {depth}")
                return depth
        
        payload = {
            "table": "scan_queue",
            "query": {
                "filter": {"status": "pending"},
                "columns": ["COUNT(*) as count"]
            }
        }
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            rows = data.get("rows", [])
            if rows:
                depth = rows[0].get("count", 0)
                logger.info(f"Queue depth from DB: {depth}")
                return depth
        return 0
    except Exception as e:
        logger.error(f"Error checking queue: {e}")
        return -1


def check_write_service_connectivity() -> dict:
    """Test write_service connectivity."""
    try:
        start = time.time()
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": "diagnostics", "rows": {"test": "connectivity_check"}, "wait": True},
            timeout=10
        )
        latency_ms = int((time.time() - start) * 1000)
        connected = response.status_code == 200
        logger.info(f"write_service connectivity: {connected}, latency: {latency_ms}ms")
        return {"connected": connected, "latency_ms": latency_ms}
    except requests.exceptions.ConnectionError:
        logger.error("write_service unreachable")
        return {"connected": False, "latency_ms": -1}
    except Exception as e:
        logger.error(f"write_service error: {e}")
        return {"connected": False, "latency_ms": -1, "error": str(e)}


def calculate_staleness(last_heartbeat_str: Optional[str]) -> dict:
    """Calculate how stale the service is."""
    if not last_heartbeat_str:
        return {"stale_seconds": -1, "is_stale": True, "threshold_exceeded": True}
    
    try:
        if isinstance(last_heartbeat_str, str):
            last_ts = datetime.fromisoformat(last_heartbeat_str.replace("Z", "+00:00"))
        else:
            last_ts = last_heartbeat_str
        
        now = datetime.now(last_ts.tzinfo) if last_ts.tzinfo else datetime.now()
        stale_seconds = int((now - last_ts).total_seconds())
        is_stale = stale_seconds > 300
        exceeded = stale_seconds > HEARTBEAT_THRESHOLD_SECONDS
        
        return {
            "stale_seconds": stale_seconds,
            "is_stale": is_stale,
            "threshold_exceeded": exceeded
        }
    except Exception as e:
        logger.error(f"Error calculating staleness: {e}")
        return {"stale_seconds": -1, "is_stale": True, "threshold_exceeded": True}


def generate_recommendation(findings: dict) -> str:
    """Generate recommendation based on findings."""
    process_alive = findings.get("process_alive", False)
    ws_connected = findings.get("write_service_connectivity", {}).get("connected", False)
    staleness = findings.get("staleness", {})
    stale_seconds = staleness.get("stale_seconds", -1)
    
    if not ws_connected:
        return "CRITICAL: write_service is unreachable. Check network/firewall. Cannot determine mcp_scanner status."
    
    if not process_alive:
        if stale_seconds > HEARTBEAT_THRESHOLD_SECONDS:
            return "CRITICAL: Process dead and heartbeat stale. Restart mcp_scanner daemon immediately."
        return "WARNING: Process not found but may have restarted. Verify service status."
    
    if stale_seconds > HEARTBEAT_THRESHOLD_SECONDS and ws_connected:
        return "WARNING: Process running but heartbeat is stale. Check if service is blocked/hung. Consider restart if issue persists."
    
    if findings.get("queue_depth", 0) > 100:
        return "INFO: Queue backup detected. Service may be processing slowly. Monitor queue drain rate."
    
    return "OK: No issues detected. Service appears healthy."


def run_diagnostic() -> dict:
    """Run full diagnostic and return findings."""
    findings = {}
    
    logger.info("Starting mcp_scanner staleness diagnostic")
    
    findings["process_alive"] = check_process_alive()
    findings["last_log_entry"] = get_last_log_entry()
    findings["last_heartbeat"] = get_last_heartbeat()
    findings["queue_depth"] = get_queue_depth()
    findings["write_service_connectivity"] = check_write_service_connectivity()
    
    findings["staleness"] = calculate_staleness(findings["last_heartbeat"])
    findings["recommendation"] = generate_recommendation(findings)
    findings["diagnostic_timestamp"] = datetime.utcnow().isoformat() + "Z"
    findings["heartbeat_threshold_seconds"] = HEARTBEAT_THRESHOLD_SECONDS
    
    return findings


def send_heartbeat():
    """Send diagnostic heartbeat to write_service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": "diagnose_mcp_scanner_stale",
                "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                "status": "running"
            },
            "wait": True
        }
        requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
    except Exception:
        pass


def main():
    """Main entry point."""
    logger.info("diagnose_mcp_scanner_stale starting")
    
    findings = run_diagnostic()
    
    print(json.dumps(findings, indent=2, default=str))
    
    send_heartbeat()
    logger.info("Diagnostic complete")
    
    return 0


if __name__ == "__main__":
    exit(main())