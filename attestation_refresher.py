import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SERVICE_NAME = "attestation_refresher"
PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)

EXPIRY_THRESHOLD_DAYS = 7
STALE_THRESHOLD_DAYS = 90
CYCLE_INTERVAL_SECS = 86400


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_write(table, rows):
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def ws_execute(sql, params=None):
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance():
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error("Another instance is running with PID %d. Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            log.warning("Stale PID file found. Removing.")
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    log.info("Received signal %d. Shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status="ok", meta=None):
    now = utc_now_iso()
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": now,
        "status": status,
        "ts": now
    }
    if meta:
        import json
        row["meta"] = json.dumps(meta)
    try:
        ws_write("service_health", [row])
    except Exception as e:
        log.warning("Failed to send heartbeat: %s", e)


def ensure_tables():
    ws_execute("""
        CREATE SEQUENCE IF NOT EXISTS attestation_refresh_id_seq
    """)
    ws_execute("""
        CREATE TABLE IF NOT EXISTS attestation_refresh_log (
            id BIGINT DEFAULT nextval('attestation_refresh_id_seq') PRIMARY KEY,
            attestation_id VARCHAR,
            server_id VARCHAR,
            action VARCHAR,
            archived_at TIMESTAMPTZ,
            new_attestation_id VARCHAR,
            error_message TEXT
        )
    """)
    log.info("Verified attestation_refresh_log table exists")


def get_expiring_attestations():
    now = utc_now_iso()
    sql = """
        SELECT 
            a.attestation_id,
            a.server_id,
            a.attestation_text AS content,
            a.generated_at AS attested_at,
            a.valid_until AS expires_at,
            a.generated_at AS regenerated_at,
            r.name as server_name,
            r.url as server_url
        FROM mcp_attestations a
        JOIN mcp_server_registry r ON a.server_id = r.server_id
        WHERE a.valid_until <= (now() + INTERVAL '7 days')
          AND (a.generated_at IS NULL OR a.generated_at < (now() - INTERVAL '90 days'))
        ORDER BY a.valid_until ASC
        LIMIT 100
    """
    return ws_query(sql)


def archive_attestation(attestation_id, server_id, action, error_msg=None):
    now = utc_now_iso()
    row = {
        "attestation_id": attestation_id,
        "server_id": server_id,
        "action": action,
        "archived_at": now
    }
    if error_msg:
        row["error_message"] = error_msg
    try:
        ws_write("attestation_refresh_log", [row])
    except Exception as e:
        log.warning("Failed to log archive action: %s", e)


def regenerate_attestation(server_id, server_name, server_url):
    now = utc_now_iso()
    attestation_id = f"ATT-{server_id}-{int(time.time())}"
    
    new_content = {
        "attestation_type": "automated_refresh",
        "original_server_id": server_id,
        "refresh_timestamp": now,
        "attestor": "attestation_refresher",
        "rationale": "Automated attestation refresh - original attestation approaching expiry"
    }
    
    import json
    content_json = json.dumps(new_content)
    
    expires_at = datetime.now(timezone.utc) + timedelta(days=365)
    expires_at_str = expires_at.isoformat()
    
    row = {
        "attestation_id": attestation_id,
        "server_id": server_id,
        "attestation_text": content_json,
        "generated_at": now,
        "valid_until": expires_at_str,
    }
    
    ws_write("mcp_attestations", [row])
    
    return attestation_id


def cycle():
    log.info("Starting attestation refresh cycle")
    
    try:
        ensure_tables()
    except Exception as e:
        log.error("Failed to ensure tables: %s", e)
        return
    
    expiring = get_expiring_attestations()
    
    if not expiring:
        log.info("No attestations requiring refresh at this time")
        return
    
    log.info("Found %d attestations requiring refresh", len(expiring))
    
    success_count = 0
    failure_count = 0
    
    for att in expiring:
        attestation_id = att.get("attestation_id")
        server_id = att.get("server_id")
        server_name = att.get("server_name", "unknown")
        server_url = att.get("server_url", "")
        expires_at = att.get("expires_at", "")
        
        log.info("Processing refresh for server %s (%s), expires %s", 
                 server_name, server_id, expires_at)
        
        try:
            new_attestation_id = regenerate_attestation(
                server_id, server_name, server_url
            )
            
            archive_attestation(
                attestation_id, 
                server_id, 
                "refreshed", 
                f"New attestation: {new_attestation_id}"
            )
            
            log.info("Successfully refreshed attestation for %s: %s -> %s",
                     server_id, attestation_id, new_attestation_id)
            success_count += 1
            
        except Exception as e:
            log.error("Failed to refresh attestation for %s: %s", server_id, e)
            archive_attestation(
                attestation_id, 
                server_id, 
                "failed", 
                str(e)
            )
            failure_count += 1
    
    log.info("Refresh cycle complete: %d successes, %d failures", 
             success_count, failure_count)
    
    send_heartbeat(
        status="ok" if failure_count == 0 else "degraded",
        meta={
            "processed": len(expiring),
            "success": success_count,
            "failures": failure_count
        }
    )


def run():
    log.info("Starting %s daemon", SERVICE_NAME)
    
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        ensure_tables()
    except Exception as e:
        log.warning("Table initialization warning (may be expected if already exists): %s", e)
    
    while True:
        try:
            cycle()
        except Exception as e:
            log.error("Cycle error: %s", e)
            send_heartbeat(status="error", meta={"error": str(e)})
        
        log.info("Sleeping for %d seconds until next cycle", CYCLE_INTERVAL_SECS)
        time.sleep(CYCLE_INTERVAL_SECS)


if __name__ == "__main__":
    run()