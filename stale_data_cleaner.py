#!/usr/bin/env python3
"""
stale_data_cleaner.py -- ZO-SENTINEL Stale Data Cleaner Daemon.
Weekly cleanup: marks stale signals inactive, expires old attestations,
marks stale registries, archives low-importance old memories.
Never hard-deletes data.
"""
import requests
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

SERVICE_NAME = "stale_data_cleaner"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 86400

log = logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> list:
    """Execute read query via write_service query endpoint."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", data.get("data", []))
    except Exception as e:
        log.warning(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write rows via write_service POST /write endpoint."""
    payload = {"table": table, "rows": rows}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"Write failed to {table}: {e}")
        return False


def send_heartbeat() -> bool:
    """Send heartbeat to service_health table."""
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat()
    })


def check_single_instance() -> bool:
    """Ensure only one instance runs via heartbeat lock."""
    rows = ws_query("""
        SELECT last_heartbeat FROM service_health
        WHERE service = :svc
        ORDER BY last_heartbeat DESC LIMIT 1
    """, {"svc": SERVICE_NAME})
    
    if rows:
        last = rows[0].get("last_heartbeat") or rows[0].get("last_heartbeat_")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                age = (datetime.utcnow() - last_dt).total_seconds()
                if age < HEARTBEAT_INTERVAL * 2:
                    log.info(f"Another instance active, exiting")
                    return False
            except Exception:
                pass
    
    send_heartbeat()
    return True


def create_tables() -> None:
    """Create required tables if they don't exist."""
    table_defs = [
        """
        CREATE TABLE IF NOT EXISTS mcp_stale_cleanup_log (
            id BIGINT PRIMARY KEY,
            cleanup_type VARCHAR,
            records_processed INTEGER,
            cleanup_timestamp TIMESTAMPTZ DEFAULT now(),
            details TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mesh_events (
            id BIGINT PRIMARY KEY,
            event_type VARCHAR,
            source VARCHAR,
            payload JSON,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """
    ]
    for sql in table_defs:
        try:
            requests.post(EXECUTE_URL, json={"sql": sql}, timeout=10)
        except Exception as e:
            log.warning(f"Table creation skipped: {e}")


def mark_stale_signals_inactive() -> int:
    """Mark mcp_signal_scores older than 90d for reassessed servers as inactive."""
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
    
    query = """
        SELECT DISTINCT ss.server_id, ss.id, ss.scored_at
        FROM mcp_signal_scores ss
        WHERE ss.scored_at < :cutoff
          AND ss.is_active IS NOT FALSE
          AND EXISTS (
              SELECT 1 FROM mcp_server_registry r
              WHERE r.server_id = ss.server_id
                AND r.last_assessed > :cutoff
          )
    """
    old_scores = ws_query(query, {"cutoff": cutoff})
    count = len(old_scores)
    
    if count > 0:
        updates = [
            {
                "id": s["id"],
                "server_id": s["server_id"],
                "signal_name": "STALE_MARKER",
                "is_active": False,
                "marked_inactive_at": datetime.utcnow().isoformat()
            }
            for s in old_scores
        ]
        ws_write("mcp_signal_scores", updates)
    
    log.info(f"Marked {count} stale signals inactive")
    return count


def expire_old_attestations() -> int:
    """Find mcp_attestations past valid_until and mark as expired."""
    now = datetime.utcnow().isoformat()
    
    query = """
        SELECT id, server_id FROM mcp_attestations
        WHERE valid_until < :now
          AND status != 'expired'
    """
    expired = ws_query(query, {"now": now})
    count = len(expired)
    
    if count > 0:
        updates = [
            {
                "id": a["id"],
                "server_id": a["server_id"],
                "status": "expired",
                "expired_at": datetime.utcnow().isoformat()
            }
            for a in expired
        ]
        ws_write("mcp_attestations", updates)
    
    log.info(f"Expired {count} old attestations")
    return count


def mark_stale_registries() -> int:
    """Find mcp_server_registry with no recent assessment and mark as stale."""
    cutoff = (datetime.utcnow() - timedelta(days=60)).isoformat()
    
    query = """
        SELECT id, server_id, name, last_assessed, last_seen
        FROM mcp_server_registry
        WHERE (last_assessed < :cutoff OR last_assessed IS NULL)
          AND last_seen < :cutoff
          AND (status IS NULL OR status NOT IN ('stale', 'archived'))
    """
    stale = ws_query(query, {"cutoff": cutoff})
    count = len(stale)
    
    if count > 0:
        updates = [
            {
                "id": r["id"],
                "server_id": r["server_id"],
                "status": "stale",
                "marked_stale_at": datetime.utcnow().isoformat()
            }
            for r in stale
        ]
        ws_write("mcp_server_registry", updates)
    
    log.info(f"Marked {count} stale registries")
    return count


def archive_low_importance_memories() -> int:
    """Archive mesh_memory older than 30d with importance < 0.5."""
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
    
    query = """
        SELECT id, server_id, memory_key, importance, created_at
        FROM mesh_memory
        WHERE created_at < :cutoff
          AND importance < 0.5
          AND (status IS NULL OR status != 'archived')
    """
    to_archive = ws_query(query, {"cutoff": cutoff})
    count = len(to_archive)
    
    if count > 0:
        updates = [
            {
                "id": m["id"],
                "server_id": m["server_id"],
                "memory_key": m["memory_key"],
                "status": "archived",
                "archived_at": datetime.utcnow().isoformat()
            }
            for m in to_archive
        ]
        ws_write("mesh_memory", updates)
    
    log.info(f"Archived {count} low-importance memories")
    return count


def log_cleanup_results(
    signal_count: int,
    attestation_count: int,
    registry_count: int,
    memory_count: int
) -> None:
    """Log cleanup results to mcp_stale_cleanup_log and mesh_events."""
    total = signal_count + attestation_count + registry_count + memory_count
    details = (
        f"signals={signal_count}, attestations={attestation_count}, "
        f"registries={registry_count}, memories={memory_count}"
    )
    
    ws_write("mcp_stale_cleanup_log", {
        "cleanup_type": "weekly_stale_cleanup",
        "records_processed": total,
        "cleanup_timestamp": datetime.utcnow().isoformat(),
        "details": details
    })
    
    ws_write("mesh_events", {
        "event_type": "cleanup_complete",
        "source": SERVICE_NAME,
        "payload": {
            "cleanup_type": "weekly_stale_cleanup",
            "total_processed": total,
            "details": details
        },
        "created_at": datetime.utcnow().isoformat()
    })


def run_cleanup() -> None:
    """Execute all cleanup operations."""
    if not check_single_instance():
        return
    
    create_tables()
    
    signal_count = mark_stale_signals_inactive()
    attestation_count = expire_old_attestations()
    registry_count = mark_stale_registries()
    memory_count = archive_low_importance_memories()
    
    log_cleanup_results(
        signal_count,
        attestation_count,
        registry_count,
        memory_count
    )
    
    send_heartbeat()


def run() -> None:
    """Main daemon loop."""
    log.info(f"Starting {SERVICE_NAME} daemon")
    
    while True:
        now = datetime.utcnow()
        
        if now.weekday() == 0:
            log.info("Weekly cleanup triggered (Monday)")
            try:
                run_cleanup()
            except Exception as e:
                log.error(f"Cleanup failed: {e}")
        else:
            log.debug(f"Not Monday (day {now.weekday()}), skipping cleanup")
        
        send_heartbeat()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()