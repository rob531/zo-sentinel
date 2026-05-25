import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import requests

SERVICE_NAME = "mcp_traffic_fingerprints_scanner_wiring"
PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = "/home/workspace/logs/mcp_traffic_fingerprints_scanner_wiring.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
POLL_SECS = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)


def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    log.info("Received signal %d, shutting down gracefully", signum)
    remove_pid_file()
    sys.exit(0)


def check_single_instance():
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            os.kill(old_pid, 0)
            log.warning("Already running with PID %d, exiting", old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to DuckDB via write_service."""
    try:
        r = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=15
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed for table %s: %s", table, e)
        return False


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service."""
    try:
        r = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30
        )
        if r.status_code == 200:
            result = r.json()
            return result.get("rows", [])
        return []
    except Exception as e:
        log.error("ws_query failed: %s", e)
        return []


def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "meta": {"version": "1.0.0", "wired_module": "mcp_traffic_fingerprints"}
        }])
    except Exception as e:
        log.warning("Heartbeat failed: %s", e)


def compute_deterministic_id(server_id: str, signal_name: str) -> str:
    """Compute deterministic ID for idempotent writes."""
    import hashlib
    content = f"{server_id}:{signal_name}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_db_path() -> str:
    return "/home/workspace/Datasets/zo-sentinel/sentinel.db"


def ensure_protocol_confirmation_table():
    """Ensure mcp_signal_scores table has protocol_confirmation signal support."""
    pass


def detect_protocol_in_response(response_body: str, server_id: str, scan_timestamp: str) -> Optional[Dict[str, Any]]:
    """
    Analyze response body for MCP protocol signatures using mcp_traffic_fingerprints.
    
    Args:
        response_body: Raw response text from candidate server
        server_id: Server identifier
        scan_timestamp: ISO timestamp of scan
    
    Returns:
        Dict with protocol confirmation details or None
    """
    try:
        from mcp_traffic_fingerprints import detect_mcp_methods, is_mcp_traffic
        
        if not is_mcp_traffic(response_body):
            return None
        
        detected_methods = detect_mcp_methods(response_body)
        
        if not detected_methods:
            return None
        
        return {
            "server_id": server_id,
            "protocol_confirmed": True,
            "detected_methods": detected_methods,
            "scan_timestamp": scan_timestamp,
            "response_sample": response_body[:500] if response_body else ""
        }
    except ImportError as e:
        log.warning("Could not import mcp_traffic_fingerprints: %s", e)
        return None
    except Exception as e:
        log.error("Protocol detection error: %s", e)
        return None


def write_protocol_confirmation_signal(server_id: str, detected_methods: List[str], 
                                         evidence_blob: Dict[str, Any], 
                                         confidence: float = 0.85) -> bool:
    """
    Write protocol_confirmation signal to mcp_signal_scores.
    Schema: id, server_id, signal_name, score, evidence, scored_at
    
    Args:
        server_id: Server identifier
        detected_methods: List of MCP methods detected
        evidence_blob: Structured evidence of detection
        confidence: Detection confidence (default 0.85)
    
    Returns:
        True if write succeeded
    """
    scored_at = datetime.now(timezone.utc).isoformat()
    
    row = {
        "server_id": server_id,
        "signal_name": "protocol_confirmation",
        "score": confidence,
        "evidence": evidence_blob if isinstance(evidence_blob, str) else json.dumps(evidence_blob),
        "scored_at": scored_at,
    }
    
    return ws_write("mcp_signal_scores", [row])


def get_unscanned_servers(limit: int = 100) -> List[Dict[str, Any]]:
    """Get servers that haven't been protocol-confirmed recently."""
    sql = f"""
    SELECT 
        server_id,
        name,
        url,
        description,
        scan_count,
        last_scanned
    FROM mcp_server_registry
    WHERE scan_count > 0
    AND server_id NOT IN (
        SELECT server_id 
        FROM mcp_signal_scores 
        WHERE signal_name = 'protocol_confirmation'
        AND scored_at > NOW() - INTERVAL '7 days'
    )
    ORDER BY scan_count DESC
    LIMIT {limit}
    """
    return ws_query(sql)


def scan_server_for_protocol(server_id: str, url: str) -> Optional[Dict[str, Any]]:
    """
    Scan a server URL for MCP protocol signatures.
    
    Args:
        server_id: Server identifier
        url: Server URL to probe
    
    Returns:
        Protocol detection result or None
    """
    if not url:
        return None
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    try:
        response = requests.get(
            url,
            timeout=10,
            headers={"Accept": "application/json, */*"}
        )
        
        if response.status_code == 200:
            response_body = response.text
            
            result = detect_protocol_in_response(response_body, server_id, timestamp)
            
            if result and result.get("protocol_confirmed"):
                return {
                    "server_id": server_id,
                    "url": url,
                    "confirmed": True,
                    "methods": result.get("detected_methods", []),
                    "timestamp": timestamp,
                    "response_sample": result.get("response_sample", "")[:200]
                }
    except Exception as e:
        log.debug("Scan failed for %s (%s): %s", server_id, url, e)
    
    return None


def enrich_server_with_protocol(server_id: str, scan_result: Dict[str, Any]) -> bool:
    """
    Write protocol confirmation signal for a server.
    
    Args:
        server_id: Server identifier
        scan_result: Protocol detection result
    
    Returns:
        True if signal written successfully
    """
    if not scan_result or not scan_result.get("confirmed"):
        return False
    
    detected_methods = scan_result.get("methods", [])
    if not detected_methods:
        return False
    
    evidence_blob = {
        "signal_type": "protocol_confirmation",
        "source": "mcp_traffic_fingerprints_scanner_wiring",
        "version": "1.0.0",
        "methods_detected": detected_methods,
        "method_count": len(detected_methods),
        "url_scanned": scan_result.get("url", ""),
        "scan_timestamp": scan_result.get("timestamp", ""),
        "response_sample_preview": scan_result.get("response_sample", "")[:200]
    }
    
    return write_protocol_confirmation_signal(
        server_id=server_id,
        detected_methods=detected_methods,
        evidence_blob=evidence_blob,
        confidence=0.85
    )


def update_server_registry_with_protocol(server_id: str, confirmed: bool, method_count: int):
    """Update mcp_server_registry with protocol confirmation metadata."""
    sql = f"""
    UPDATE mcp_server_registry 
    SET meta = COALESCE(meta, '{{}}'::JSONB) || 
        JSONB_BUILD_OBJECT(
            'protocol_confirmed', {confirmed},
            'protocol_methods_count', {method_count},
            'protocol_checked_at', '{datetime.now(timezone.utc).isoformat()}'
        )
    WHERE server_id = '{server_id}'
    """
    try:
        r = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error("Failed to update registry for %s: %s", server_id, e)
        return False


def cycle() -> int:
    """
    Process one cycle of protocol confirmation scanning.
    
    Returns:
        Number of servers processed
    """
    log.info("Starting protocol confirmation cycle")
    
    servers = get_unscanned_servers(limit=50)
    if not servers:
        log.info("No servers pending protocol confirmation")
        return 0
    
    log.info("Found %d servers to protocol-scan", len(servers))
    
    processed = 0
    
    for server in servers:
        server_id = server.get("server_id")
        url = server.get("url")
        name = server.get("name", "unknown")
        
        if not server_id or not url:
            continue
        
        log.debug("Scanning %s (%s) for MCP protocol", name, server_id)
        
        scan_result = scan_server_for_protocol(server_id, url)
        
        if scan_result and scan_result.get("confirmed"):
            enriched = enrich_server_with_protocol(server_id, scan_result)
            
            if enriched:
                update_server_registry_with_protocol(
                    server_id,
                    confirmed=True,
                    method_count=len(scan_result.get("methods", []))
                )
                log.info("Protocol confirmed for %s: %s", 
                         server_id, scan_result.get("methods", []))
        
        processed += 1
        time.sleep(1)
    
    log.info("Protocol confirmation cycle complete: %d servers processed", processed)
    return processed


def heartbeat_loop():
    """Background heartbeat thread."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.warning("Heartbeat error: %s", e)
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main daemon run loop."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info("Starting %s daemon", SERVICE_NAME)
    
    send_heartbeat()
    
    while True:
        try:
            processed = cycle()
            send_heartbeat()
        except Exception as e:
            log.error("Cycle error: %s", e)
            send_heartbeat()
        
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()