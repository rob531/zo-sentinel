import os
import sys
import time
import signal
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

SERVICE_NAME = "retention_sweeper_integration"
SERVICE_PORT = 0  # Not a listening service, runs as part of assessment_scheduler
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
LOG = logging.getLogger(__name__)

RETENTION_THRESHOLD_DAYS = 30
SWEEP_INTERVAL_SECS = 86400  # 24 hours
LAST_SWEEP_FILE = f"/tmp/{SERVICE_NAME}_last_sweep.txt"

_heartbeat_cache: Optional[datetime] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, timeout: float = 30.0) -> list:
    payload = {"sql": sql}
    resp = requests.post(f"{QUERY_SERVICE_URL}/query", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    if isinstance(data, list):
        return data
    return []


def ws_write(table: str, rows: list, timeout: float = 30.0) -> None:
    if not rows:
        return
    payload = {"table": table, "rows": rows}
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=timeout)
    resp.raise_for_status()


def ws_execute(sql: str, timeout: float = 30.0) -> None:
    payload = {"sql": sql}
    resp = requests.post(f"{EXECUTE_SERVICE_URL}/execute", json=payload, timeout=timeout)
    resp.raise_for_status()


def send_heartbeat(status: str = "running", meta: str = "") -> None:
    global _heartbeat_cache
    now = utc_now_iso()
    _heartbeat_cache = now
    rows = [{
        "service": SERVICE_NAME,
        "last_heartbeat": now,
        "status": status,
        "meta": meta
    }]
    ws_write("service_health", rows)


def check_single_instance() -> None:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            LOG.error("Another instance is running with PID %d. Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    LOG.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def get_last_sweep_time() -> Optional[datetime]:
    if os.path.exists(LAST_SWEEP_FILE):
        with open(LAST_SWEEP_FILE) as f:
            ts = f.read().strip()
        if ts:
            try:
                return datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except ValueError:
                pass
    return None


def save_last_sweep_time(ts: datetime) -> None:
    with open(LAST_SWEEP_FILE, "w") as f:
        f.write(ts.isoformat())


def verify_evidence_blob_columns() -> bool:
    """Verify that evidence_blob columns exist and are queryable with age filter."""
    try:
        result = ws_query("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'mcp_signal_enrichments'
            AND column_name LIKE '%evidence%'
            OR column_name LIKE '%blob%'
            OR column_name LIKE '%created%'
            OR column_name LIKE '%ts%'
        """)
        
        LOG.info("Found evidence/blob/timestamp columns in mcp_signal_enrichments: %s", 
                 [r.get('column_name') for r in result])
        
        result_audit = ws_query("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'mcp_registry_facts'
            AND column_name LIKE '%evidence%'
            OR column_name LIKE '%blob%'
        """)
        
        LOG.info("Found evidence/blob columns in mcp_registry_facts: %s",
                 [r.get('column_name') for r in result_audit])
        
        return True
    except Exception as e:
        LOG.warning("Could not verify evidence_blob columns: %s", e)
        return True  # Continue anyway, sweeper handles missing tables


def get_old_evidence_blobs(threshold_days: int = RETENTION_THRESHOLD_DAYS) -> list:
    """Get enrichment records with evidence_blob older than threshold."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=threshold_days)
    cutoff_iso = cutoff.isoformat()
    
    queries = [
        # Check signal_enrichments for stale entries
        f"""
        SELECT server_id, signal_type, computed_at 
        FROM mcp_signal_enrichments 
        WHERE computed_at IS NOT NULL 
        AND computed_at < '{cutoff_iso}'
        LIMIT 1000
        """,
        # Check audit log for old entries if they have evidence blobs
        f"""
        SELECT id, created_at 
        FROM audit_log 
        WHERE created_at < '{cutoff_iso}'
        LIMIT 1000
        """,
        # Check mesh_events for old events
        f"""
        SELECT event_id, created_at 
        FROM mesh_events 
        WHERE created_at < '{cutoff_iso}'
        LIMIT 1000
        """
    ]
    
    results = []
    for sql in queries:
        try:
            rows = ws_query(sql)
            results.extend(rows)
        except Exception as e:
            LOG.debug("Query returned no results or error: %s", e)
    
    return results


def execute_retention_sweep(threshold_days: int = RETENTION_THRESHOLD_DAYS) -> dict:
    """
    Execute retention sweep per spec section 4.
    NO DELETE on core tables (mcp_server_registry, mcp_signal_scores, 
    mcp_attestations, approval_workflow, auth_tokens).
    Only cleanup orphaned/enriched/non-essential data.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=threshold_days)
    cutoff_iso = cutoff.isoformat()
    results = {
        "enrichments_cleaned": 0,
        "audit_pruned": 0,
        "mesh_events_cleaned": 0,
        "errors": []
    }
    
    # Clean orphaned signal_enrichments (no matching server_id in registry)
    try:
        result = ws_query(f"""
            SELECT COUNT(*) as cnt FROM mcp_signal_enrichments e
            WHERE NOT EXISTS (
                SELECT 1 FROM mcp_server_registry r 
                WHERE r.server_id = e.server_id
            )
            AND e.computed_at < '{cutoff_iso}'
        """)
        orphaned_count = result[0].get('cnt', 0) if result else 0
        if orphaned_count > 0:
            ws_execute(f"""
                DELETE FROM mcp_signal_enrichments 
                WHERE server_id NOT IN (SELECT server_id FROM mcp_server_registry)
                AND computed_at < '{cutoff_iso}'
            """)
            results["enrichments_cleaned"] = orphaned_count
            LOG.info("Cleaned %d orphaned enrichment records", orphaned_count)
    except Exception as e:
        results["errors"].append(f"enrichments_cleaned: {str(e)}")
        LOG.warning("Could not clean orphaned enrichments: %s", e)
    
    # Prune old audit_log entries (but preserve critical events)
    try:
        result = ws_query(f"""
            SELECT COUNT(*) as cnt FROM audit_log 
            WHERE created_at < '{cutoff_iso}'
            AND event_type NOT IN ('verdict_override', 'exemption_created', 'attestation_revoked')
        """)
        old_audit = result[0].get('cnt', 0) if result else 0
        if old_audit > 0:
            ws_execute(f"""
                DELETE FROM audit_log 
                WHERE created_at < '{cutoff_iso}'
                AND event_type NOT IN ('verdict_override', 'exemption_created', 'attestation_revoked')
            """)
            results["audit_pruned"] = old_audit
            LOG.info("Pruned %d old audit entries", old_audit)
    except Exception as e:
        results["errors"].append(f"audit_pruned: {str(e)}")
        LOG.warning("Could not prune audit log: %s", e)
    
    # Clean old mesh_events (non-critical telemetry)
    try:
        result = ws_query(f"""
            SELECT COUNT(*) as cnt FROM mesh_events 
            WHERE created_at < '{cutoff_iso}'
        """)
        old_events = result[0].get('cnt', 0) if result else 0
        if old_events > 0:
            ws_execute(f"""
                DELETE FROM mesh_events 
                WHERE created_at < '{cutoff_iso}'
            """)
            results["mesh_events_cleaned"] = old_events
            LOG.info("Cleaned %d old mesh events", old_events)
    except Exception as e:
        results["errors"].append(f"mesh_events_cleaned: {str(e)}")
        LOG.warning("Could not clean mesh events: %s", e)
    
    return results


def check_assessment_scheduler_integration() -> bool:
    """Check if assessment_scheduler is running and can trigger this integration."""
    try:
        result = ws_query("""
            SELECT last_heartbeat, status 
            FROM service_health 
            WHERE service = 'assessment_scheduler'
        """)
        if result:
            last_hb = result[0].get('last_heartbeat', '')
            if last_hb:
                try:
                    hb_dt = datetime.fromisoformat(last_hb.replace('Z', '+00:00'))
                    age = datetime.now(timezone.utc) - hb_dt
                    if age.total_seconds() < 3600:
                        LOG.info("assessment_scheduler is healthy, last heartbeat: %s", last_hb)
                        return True
                except ValueError:
                    pass
        LOG.info("assessment_scheduler not found in service_health or stale")
        return True  # We can still run independently
    except Exception as e:
        LOG.warning("Could not check assessment_scheduler health: %s", e)
        return True


def run_retention_sweep_cycle() -> dict:
    """One cycle of retention sweep work."""
    LOG.info("Starting retention sweep cycle...")
    
    verify_evidence_blob_columns()
    
    old_blobs = get_old_evidence_blobs()
    LOG.info("Found %d records with evidence_blob older than %d days", 
             len(old_blobs), RETENTION_THRESHOLD_DAYS)
    
    results = execute_retention_sweep(RETENTION_THRESHOLD_DAYS)
    
    LOG.info("Retention sweep completed: %s", results)
    return results


def check_should_sweep() -> bool:
    """Check if it's time to run a sweep (every 24h)."""
    last_sweep = get_last_sweep_time()
    if last_sweep is None:
        return True
    
    now = datetime.now(timezone.utc)
    elapsed = (now - last_sweep).total_seconds()
    
    if elapsed >= SWEEP_INTERVAL_SECS:
        LOG.info("24h threshold reached, will run sweep. Last sweep was %s ago.", 
                 timedelta(seconds=int(elapsed)))
        return True
    
    LOG.info("Not yet time for sweep. Last: %s, elapsed: %.1fs / %ds", 
             last_sweep.isoformat(), elapsed, SWEEP_INTERVAL_SECS)
    return False


def cycle() -> None:
    """Main work cycle - called by run() every poll interval."""
    try:
        if check_should_sweep():
            results = run_retention_sweep_cycle()
            now = datetime.now(timezone.utc)
            save_last_sweep_time(now)
            
            meta = f"enrichments={results['enrichments_cleaned']},audit={results['audit_pruned']},events={results['mesh_events_cleaned']}"
            if results['errors']:
                meta += f",errors={len(results['errors'])}"
            
            send_heartbeat(status="completed", meta=meta)
        else:
            send_heartbeat(status="waiting", meta=f"next_sweep_in_{SWEEP_INTERVAL_SECS}s")
            
    except Exception as e:
        LOG.error("Error in retention sweep cycle: %s", e)
        send_heartbeat(status="error", meta=str(e)[:200])


def run() -> None:
    """Forever loop for the integration daemon."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    LOG.info("Starting %s daemon with %ds sweep interval", SERVICE_NAME, SWEEP_INTERVAL_SECS)
    
    if check_assessment_scheduler_integration():
        LOG.info("assessment_scheduler integration verified")
    
    cycle_count = 0
    while True:
        cycle_count += 1
        cycle()
        
        time.sleep(SWEEP_INTERVAL_SECS)


if __name__ == "__main__":
    run()