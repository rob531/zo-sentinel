#!/usr/bin/env python3
"""
sybil_burst_detector.py -- ZO-SENTINEL Sybil / burst request detector daemon.
Detects:
  - burst_attack: >20 assessment requests for same server_id within 60s
  - coordinated_registration: 10+ NEW server_ids registered within 5min from same registry_source
  - copy_paste_sybil: multiple servers sharing identical description text
Polls every 300s with heartbeat.
"""
import os
import sys
import time
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional

import requests

log = logging.getLogger(__name__)

SERVICE_NAME = "sybil_burst_detector"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 30
POLL_INTERVAL = 300

PID_FILE = f"/tmp/{SERVICE_NAME}.pid"


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_execute_url() -> str:
    return EXECUTE_URL


def get_query_url() -> str:
    return QUERY_URL


def get_db_path() -> str:
    return os.environ.get("SENTINEL_DB_PATH", "/tmp/sentinel.duckdb")


def check_single_instance() -> bool:
    """Ensure only one instance runs. Returns False if another instance is running."""
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance already running with PID {old_pid}")
                return False
            except OSError:
                log.info(f"Stale PID file found, removing...")
                os.remove(PID_FILE)
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.warning(f"Failed to check/create PID file: {e}")
        return True


def remove_pid_file():
    """Remove the PID file on shutdown."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def send_heartbeat():
    """Send heartbeat to write_service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat()
            }
        }
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        if response.status_code not in (200, 201):
            log.warning(f"Heartbeat failed: {response.status_code} {response.text}")
    except Exception as e:
        log.warning(f"Heartbeat error: {e}")


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query via write_service query endpoint."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        response = requests.post(QUERY_URL, json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "result" in data:
                return data["result"]
            return data if isinstance(data, list) else []
        else:
            log.warning(f"Query failed: {response.status_code} {response.text}")
            return []
    except Exception as e:
        log.error(f"Query error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]):
    """Write rows to a table via write_service. Uses 'rows' not 'row'."""
    try:
        payload = {"table": table, "rows": rows}
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        if response.status_code not in (200, 201):
            log.warning(f"Write failed for {table}: {response.status_code} {response.text}")
            return False
        return True
    except Exception as e:
        log.error(f"Write error for {table}: {e}")
        return False


def ensure_tables():
    """Ensure required tables exist."""
    create_threat_table = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence    TEXT,
        severity    VARCHAR,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    try:
        payload = {"sql": create_threat_table}
        requests.post(EXECUTE_URL, json=payload, timeout=30)
    except Exception as e:
        log.warning(f"Failed to ensure mcp_threat_associations table: {e}")


def detect_burst_attacks() -> List[Dict[str, Any]]:
    """
    Detect burst attacks: >20 assessment requests for same server_id within 60 seconds.
    Groups events by server_id and minute, counts requests in last 60 seconds.
    """
    sql = """
    WITH recent_events AS (
        SELECT 
            json_extract_string(payload, '$.server_id') AS server_id,
            event_type,
            created_at
        FROM mesh_events
        WHERE event_type IN ('assessment_requested', 'build_complete')
          AND created_at >= now() - INTERVAL '5 minutes'
    ),
    server_minute_counts AS (
        SELECT 
            server_id,
            COUNT(*) AS request_count,
            MIN(created_at) AS first_request,
            MAX(created_at) AS last_request,
            date_trunc('second', MIN(created_at)) AS window_start
        FROM recent_events
        WHERE server_id IS NOT NULL AND server_id != ''
        GROUP BY server_id
    )
    SELECT 
        server_id,
        request_count,
        window_start,
        first_request,
        last_request,
        EXTRACT(EPOCH FROM (last_request - first_request)) AS window_seconds
    FROM server_minute_counts
    WHERE request_count > 20
       OR (request_count > 15 AND EXTRACT(EPOCH FROM (last_request - first_request)) < 60)
    ORDER BY request_count DESC
    """
    return ws_query(sql)


def detect_coordinated_registrations() -> List[Dict[str, Any]]:
    """
    Detect coordinated_registration: 10+ NEW server_ids registered within 5 minutes
    from same registry_source (classic Sybil seeding pattern).
    """
    sql = """
    WITH recent_registrations AS (
        SELECT 
            server_id,
            registry_source,
            first_seen,
            ROW_NUMBER() OVER (PARTITION BY registry_source ORDER BY first_seen DESC) AS rn,
            COUNT(*) OVER (PARTITION BY registry_source) AS total_count
        FROM mcp_server_registry
        WHERE first_seen >= now() - INTERVAL '5 minutes'
          AND registry_source IS NOT NULL
    )
    SELECT 
        registry_source,
        COUNT(*) AS new_server_count,
        MIN(first_seen) AS first_registration,
        MAX(first_seen) AS last_registration,
        STRING_AGG(server_id, ', ') AS server_ids
    FROM recent_registrations
    GROUP BY registry_source, total_count
    HAVING COUNT(*) >= 10
    ORDER BY new_server_count DESC
    """
    return ws_query(sql)


def detect_copy_paste_sybil() -> List[Dict[str, Any]]:
    """
    Detect copy-paste Sybil: multiple servers sharing identical description text.
    Uses TRIM and grouping to find duplicate descriptions.
    """
    sql = """
    WITH trimmed_descriptions AS (
        SELECT 
            server_id,
            TRIM(COALESCE(description, '')) AS clean_description,
            name,
            registry_source,
            trust_score,
            first_seen
        FROM mcp_server_registry
        WHERE description IS NOT NULL 
          AND TRIM(description) != ''
    ),
    duplicate_groups AS (
        SELECT 
            clean_description,
            COUNT(*) AS server_count,
            STRING_AGG(server_id, ', ') AS server_ids,
            STRING_AGG(name, ', ') AS names,
            MIN(first_seen) AS earliest_registration,
            MAX(first_seen) AS latest_registration
        FROM trimmed_descriptions
        WHERE LENGTH(clean_description) > 20
        GROUP BY clean_description
        HAVING COUNT(*) >= 3
    )
    SELECT 
        clean_description AS shared_description,
        server_count,
        server_ids,
        names,
        earliest_registration,
        latest_registration,
        EXTRACT(EPOCH FROM (latest_registration - earliest_registration)) AS registration_span_seconds
    FROM duplicate_groups
    WHERE server_count >= 3
    ORDER BY server_count DESC
    LIMIT 20
    """
    return ws_query(sql)


def detect_suspicious_registration_velocity() -> List[Dict[str, Any]]:
    """
    Detect extremely high registration velocity - potential automated Sybil attack.
    """
    sql = """
    WITH hourly_velocity AS (
        SELECT 
            registry_source,
            date_trunc('hour', first_seen) AS hour_bucket,
            COUNT(*) AS registration_count,
            COUNT(DISTINCT name) AS unique_names,
            COUNT(DISTINCT url) AS unique_urls
        FROM mcp_server_registry
        WHERE first_seen >= now() - INTERVAL '1 hour'
          AND registry_source IS NOT NULL
        GROUP BY registry_source, hour_bucket
    )
    SELECT 
        registry_source,
        hour_bucket,
        registration_count,
        unique_names,
        unique_urls,
        ROUND(100.0 * unique_names / registration_count, 2) AS unique_name_pct
    FROM hourly_velocity
    WHERE registration_count > 50
      AND unique_name_pct < 70
    ORDER BY registration_count DESC
    """
    return ws_query(sql)


def is_already_reported(server_id: str, threat_type: str, lookback_minutes: int = 60) -> bool:
    """Check if a threat has already been reported for this server_id recently."""
    sql = """
    SELECT COUNT(*) AS cnt
    FROM mcp_threat_associations
    WHERE server_id = ?
      AND threat_type = ?
      AND reported_at >= now() - INTERVAL '? minutes'
    """
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": [server_id, threat_type, lookback_minutes]},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("cnt", 0) > 0
            if isinstance(data, dict) and "result" in data:
                result = data["result"]
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get("cnt", 0) > 0
        return False
    except Exception as e:
        log.warning(f"Failed to check existing report: {e}")
        return False


def is_collective_already_reported(registry_source: str, threat_type: str, lookback_minutes: int = 60) -> bool:
    """Check if a collective threat has already been reported for this registry_source recently."""
    sql = """
    SELECT COUNT(*) AS cnt
    FROM mcp_threat_associations
    WHERE json_extract_string(evidence, '$.registry_source') = ?
      AND threat_type = ?
      AND reported_at >= now() - INTERVAL '? minutes'
    """
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": [registry_source, threat_type, lookback_minutes]},
            timeout=30
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("cnt", 0) > 0
            if isinstance(data, dict) and "result" in data:
                result = data["result"]
                if isinstance(result, list) and len(result) > 0:
                    return result[0].get("cnt", 0) > 0
        return False
    except Exception as e:
        log.warning(f"Failed to check existing collective report: {e}")
        return False


def report_burst_attack(server_id: str, request_count: int, window_seconds: float):
    """Report a burst attack threat."""
    evidence = {
        "threat_type": "sybil_burst",
        "request_count": request_count,
        "window_seconds": window_seconds,
        "detection_type": "burst_attack"
    }
    rows = [{
        "server_id": server_id,
        "threat_type": "sybil_burst",
        "severity": "HIGH",
        "evidence": str(evidence)
    }]
    if ws_write("mcp_threat_associations", rows):
        log.warning(f"REPORTED burst_attack: server_id={server_id}, requests={request_count}, window={window_seconds:.1f}s")


def report_coordinated_registration(registry_source: str, server_count: int, server_ids: str):
    """Report coordinated registration threat."""
    evidence = {
        "threat_type": "coordinated_registration",
        "registry_source": registry_source,
        "server_count": server_count,
        "server_ids": server_ids,
        "detection_type": "sybil_seeding"
    }
    rows = [{
        "server_id": f"collective:{registry_source}",
        "threat_type": "coordinated_registration",
        "severity": "HIGH",
        "evidence": str(evidence)
    }]
    if ws_write("mcp_threat_associations", rows):
        log.warning(f"REPORTED coordinated_registration: registry={registry_source}, count={server_count}")


def report_copy_paste_sybil(server_ids: List[str], shared_description: str, server_count: int):
    """Report copy-paste Sybil threat for each server in the group."""
    for server_id in server_ids:
        evidence = {
            "threat_type": "copy_paste_sybil",
            "shared_description": shared_description[:500],
            "group_size": server_count,
            "detection_type": "description_duplication"
        }
        rows = [{
            "server_id": server_id,
            "threat_type": "copy_paste_sybil",
            "severity": "MEDIUM",
            "evidence": str(evidence)
        }]
        if ws_write("mcp_threat_associations", rows):
            log.warning(f"REPORTED copy_paste_sybil: server_id={server_id}, group_size={server_count}")


def report_high_velocity_registration(registry_source: str, registration_count: int, unique_pct: float):
    """Report high velocity registration threat."""
    evidence = {
        "threat_type": "high_velocity_registration",
        "registry_source": registry_source,
        "registration_count": registration_count,
        "unique_name_pct": unique_pct,
        "detection_type": "velocity_analysis"
    }
    rows = [{
        "server_id": f"collective:{registry_source}",
        "threat_type": "high_velocity_registration",
        "severity": "MEDIUM",
        "evidence": str(evidence)
    }]
    if ws_write("mcp_threat_associations", rows):
        log.warning(f"REPORTED high_velocity_registration: registry={registry_source}, count={registration_count}")


def cycle():
    """Main detection cycle."""
    log.info("Starting Sybil/burst detection cycle")
    
    try:
        ensure_tables()
        
        burst_count = 0
        coord_reg_count = 0
        copy_paste_count = 0
        velocity_count = 0
        
        burst_attacks = detect_burst_attacks()
        log.info(f"Detected {len(burst_attacks)} potential burst attack patterns")
        for attack in burst_attacks:
            server_id = attack.get("server_id", "")
            request_count = attack.get("request_count", 0)
            window_seconds = attack.get("window_seconds", 0)
            if server_id and not is_already_reported(server_id, "sybil_burst"):
                report_burst_attack(server_id, request_count, window_seconds)
                burst_count += 1
            else:
                log.debug(f"Burst already reported: {server_id}")
        
        coordinated = detect_coordinated_registrations()
        log.info(f"Detected {len(coordinated)} coordinated registration patterns")
        for coord in coordinated:
            registry_source = coord.get("registry_source", "")
            server_count = coord.get("new_server_count", 0)
            server_ids = coord.get("server_ids", "")
            if registry_source and not is_collective_already_reported(registry_source, "coordinated_registration"):
                report_coordinated_registration(registry_source, server_count, server_ids)
                coord_reg_count += 1
            else:
                log.debug(f"Coordinated registration already reported: {registry_source}")
        
        copy_paste_groups = detect_copy_paste_sybil()
        log.info(f"Detected {len(copy_paste_groups)} copy-paste Sybil patterns")
        for group in copy_paste_groups:
            server_ids_str = group.get("server_ids", "")
            shared_description = group.get("shared_description", "")
            server_count = group.get("server_count", 0)
            if server_ids_str:
                server_id_list = [s.strip() for s in server_ids_str.split(",") if s.strip()]
                newly_reported = 0
                for server_id in server_id_list:
                    if not is_already_reported(server_id, "copy_paste_sybil"):
                        newly_reported += 1
                if newly_reported > 0:
                    report_copy_paste_sybil(server_id_list, shared_description, server_count)
                    copy_paste_count += len(server_id_list)
        
        velocity_patterns = detect_suspicious_registration_velocity()
        log.info(f"Detected {len(velocity_patterns)} high velocity registration patterns")
        for pattern in velocity_patterns:
            registry_source = pattern.get("registry_source", "")
            registration_count = pattern.get("registration_count", 0)
            unique_pct = pattern.get("unique_name_pct", 0)
            if registry_source and not is_collective_already_reported(registry_source, "high_velocity_registration", 30):
                report_high_velocity_registration(registry_source, registration_count, unique_pct)
                velocity_count += 1
        
        log.info(
            f"Sybil/burst detection complete: "
            f"burst_attacks={burst_count}, coordinated_reg={coord_reg_count}, "
            f"copy_paste={copy_paste_count}, velocity={velocity_count}"
        )
        
    except Exception as e:
        log.error(f"Error in detection cycle: {e}", exc_info=True)


def heartbeat_loop():
    """Background heartbeat thread."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.warning(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main daemon entry point."""
    if not check_single_instance():
        log.error("Another instance is already running. Exiting.")
        sys.exit(1)
    
    try:
        log.info(f"Starting {SERVICE_NAME} daemon")
        ensure_tables()
        send_heartbeat()
        
        while True:
            try:
                cycle()
            except Exception as e:
                log.error(f"Error in main loop: {e}", exc_info=True)
            
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log.info("Received shutdown signal")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    import threading
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    run()