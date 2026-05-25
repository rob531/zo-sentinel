#!/usr/bin/env python3
"""
audit_trail.py -- ZO-SENTINEL Immutable Audit Trail Module.
Records all significant events, decisions, and actions for compliance and forensics.
Used by approval_workflow for all decisions and all agents making verdict changes.
"""
import requests
import logging
import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"

AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id         VARCHAR UNIQUE NOT NULL,
    event_type       VARCHAR NOT NULL,
    actor            VARCHAR NOT NULL,
    target_server_id VARCHAR,
    action           VARCHAR NOT NULL,
    outcome          VARCHAR NOT NULL,
    details_json     TEXT,
    timestamp        TIMESTAMPTZ DEFAULT now(),
    immutable        BOOLEAN DEFAULT true
)
"""

def ws_write(rows: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Write to write_service via POST /write with 'rows' field."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"rows": rows},
            timeout=15
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        log.error(f"ws_write failed: {e}")
        return None

def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> Optional[List[Dict[str, Any]]]:
    """Query via write_service execute endpoint."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(
            QUERY_URL,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict) and "data" in result:
            return result["data"]
        return result if isinstance(result, list) else None
    except requests.exceptions.RequestException as e:
        log.error(f"ws_query failed: {e}")
        return None

def ensure_audit_table() -> bool:
    """Ensure the audit_log table exists."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL.replace("/write", "/execute"),
            json={"sql": AUDIT_TABLE_SQL},
            timeout=15
        )
        resp.raise_for_status()
        log.info("audit_log table ensured")
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Failed to create audit_log table: {e}")
        return False

def record_event(
    event_type: str,
    actor: str,
    target_server_id: Optional[str],
    action: str,
    outcome: str,
    details: Optional[Dict[str, Any]] = None
) -> str:
    """
    Record an immutable audit event.
    
    Args:
        event_type: Category of event (e.g., 'verdict_change', 'approval', 'scan')
        actor: Who/what performed the action (e.g., 'risk_ranker', 'analyst_jane')
        target_server_id: Server ID being acted upon (can be None for system events)
        action: Specific action taken (e.g., 'update_verdict', 'approve', 'reject')
        outcome: Result of action (e.g., 'approved', 'rejected', 'error', 'escalated')
        details: Additional context as dict
    
    Returns:
        event_id (UUID string) of the recorded event
    """
    event_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details) if details else "{}"
    
    audit_row = {
        "event_id": event_id,
        "event_type": event_type,
        "actor": actor,
        "target_server_id": target_server_id,
        "action": action,
        "outcome": outcome,
        "details_json": details_json,
        "timestamp": timestamp,
        "immutable": True
    }
    
    result = ws_write(audit_row)
    if result:
        log.info(f"Recorded audit event: {event_id} [{event_type}] {actor} -> {outcome}")
    else:
        log.warning(f"Failed to record audit event: {event_id}")
    
    return event_id

def get_server_history(server_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get full chronological audit history for a specific server.
    
    Args:
        server_id: The server to get history for
        limit: Maximum number of events to return (default 100)
    
    Returns:
        List of audit events ordered by timestamp descending
    """
    sql = f"""
        SELECT 
            event_id,
            event_type,
            actor,
            action,
            outcome,
            details_json,
            timestamp
        FROM audit_log
        WHERE target_server_id = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """
    results = ws_query(sql, {"p1": server_id, "p2": limit})
    if results is None:
        log.warning(f"No audit history found for server: {server_id}")
        return []
    
    events = []
    for row in results:
        try:
            event = {
                "event_id": row[0] if isinstance(row, (list, tuple)) else row.get("event_id"),
                "event_type": row[1] if isinstance(row, (list, tuple)) else row.get("event_type"),
                "actor": row[2] if isinstance(row, (list, tuple)) else row.get("actor"),
                "action": row[3] if isinstance(row, (list, tuple)) else row.get("action"),
                "outcome": row[4] if isinstance(row, (list, tuple)) else row.get("outcome"),
                "details_json": row[5] if isinstance(row, (list, tuple)) else row.get("details_json"),
                "timestamp": row[6] if isinstance(row, (list, tuple)) else row.get("timestamp")
            }
            if event.get("details_json"):
                try:
                    event["details"] = json.loads(event["details_json"])
                except json.JSONDecodeError:
                    event["details"] = {}
            events.append(event)
        except Exception as e:
            log.error(f"Error parsing audit event row: {e}")
            continue
    
    return events

def get_actor_history(actor: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get all audit events performed by a specific actor.
    
    Args:
        actor: Actor identifier to get history for
        limit: Maximum number of events to return (default 100)
    
    Returns:
        List of audit events ordered by timestamp descending
    """
    sql = f"""
        SELECT 
            event_id,
            event_type,
            target_server_id,
            action,
            outcome,
            details_json,
            timestamp
        FROM audit_log
        WHERE actor = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """
    results = ws_query(sql, {"p1": actor, "p2": limit})
    if results is None:
        log.warning(f"No audit history found for actor: {actor}")
        return []
    
    events = []
    for row in results:
        try:
            event = {
                "event_id": row[0] if isinstance(row, (list, tuple)) else row.get("event_id"),
                "event_type": row[1] if isinstance(row, (list, tuple)) else row.get("event_type"),
                "target_server_id": row[2] if isinstance(row, (list, tuple)) else row.get("target_server_id"),
                "action": row[3] if isinstance(row, (list, tuple)) else row.get("action"),
                "outcome": row[4] if isinstance(row, (list, tuple)) else row.get("outcome"),
                "details_json": row[5] if isinstance(row, (list, tuple)) else row.get("details_json"),
                "timestamp": row[6] if isinstance(row, (list, tuple)) else row.get("timestamp")
            }
            if event.get("details_json"):
                try:
                    event["details"] = json.loads(event["details_json"])
                except json.JSONDecodeError:
                    event["details"] = {}
            events.append(event)
        except Exception as e:
            log.error(f"Error parsing audit event row: {e}")
            continue
    
    return events

def get_events_by_type(event_type: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get all audit events of a specific type.
    
    Args:
        event_type: Type of event to filter by
        limit: Maximum number of events to return
    
    Returns:
        List of audit events
    """
    sql = f"""
        SELECT 
            event_id,
            actor,
            target_server_id,
            action,
            outcome,
            details_json,
            timestamp
        FROM audit_log
        WHERE event_type = ?
        ORDER BY timestamp DESC
        LIMIT ?
    """
    results = ws_query(sql, {"p1": event_type, "p2": limit})
    if results is None:
        return []
    
    events = []
    for row in results:
        try:
            event = {
                "event_id": row[0] if isinstance(row, (list, tuple)) else row.get("event_id"),
                "actor": row[1] if isinstance(row, (list, tuple)) else row.get("actor"),
                "target_server_id": row[2] if isinstance(row, (list, tuple)) else row.get("target_server_id"),
                "action": row[3] if isinstance(row, (list, tuple)) else row.get("action"),
                "outcome": row[4] if isinstance(row, (list, tuple)) else row.get("outcome"),
                "details_json": row[5] if isinstance(row, (list, tuple)) else row.get("details_json"),
                "timestamp": row[6] if isinstance(row, (list, tuple)) else row.get("timestamp")
            }
            if event.get("details_json"):
                try:
                    event["details"] = json.loads(event["details_json"])
                except json.JSONDecodeError:
                    event["details"] = {}
            events.append(event)
        except Exception as e:
            log.error(f"Error parsing audit event row: {e}")
            continue
    
    return events

def get_recent_events(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Get the most recent audit events across all actors.
    
    Args:
        limit: Maximum number of events to return
    
    Returns:
        List of recent audit events
    """
    sql = f"""
        SELECT 
            event_id,
            event_type,
            actor,
            target_server_id,
            action,
            outcome,
            details_json,
            timestamp
        FROM audit_log
        ORDER BY timestamp DESC
        LIMIT ?
    """
    results = ws_query(sql, {"p1": limit})
    if results is None:
        return []
    
    events = []
    for row in results:
        try:
            event = {
                "event_id": row[0] if isinstance(row, (list, tuple)) else row.get("event_id"),
                "event_type": row[1] if isinstance(row, (list, tuple)) else row.get("event_type"),
                "actor": row[2] if isinstance(row, (list, tuple)) else row.get("actor"),
                "target_server_id": row[3] if isinstance(row, (list, tuple)) else row.get("target_server_id"),
                "action": row[4] if isinstance(row, (list, tuple)) else row.get("action"),
                "outcome": row[5] if isinstance(row, (list, tuple)) else row.get("outcome"),
                "details_json": row[6] if isinstance(row, (list, tuple)) else row.get("details_json"),
                "timestamp": row[7] if isinstance(row, (list, tuple)) else row.get("timestamp")
            }
            if event.get("details_json"):
                try:
                    event["details"] = json.loads(event["details_json"])
                except json.JSONDecodeError:
                    event["details"] = {}
            events.append(event)
        except Exception as e:
            log.error(f"Error parsing audit event row: {e}")
            continue
    
    return events

def export_audit_csv(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    event_types: Optional[List[str]] = None,
    actors: Optional[List[str]] = None
) -> str:
    """
    Export audit log as CSV string.
    
    Args:
        start_date: ISO format start date (e.g., '2024-01-01')
        end_date: ISO format end date (e.g., '2024-12-31')
        event_types: Optional list of event types to filter
        actors: Optional list of actors to filter
    
    Returns:
        CSV formatted string of audit events
    """
    conditions = []
    params = {}
    param_idx = 1
    
    if start_date:
        conditions.append(f"timestamp >= '${start_date}'")
    if end_date:
        conditions.append(f"timestamp <= '${end_date}'")
    if event_types:
        type_list = "', '".join(event_types)
        conditions.append(f"event_type IN ('{type_list}')")
    if actors:
        actor_list = "', '".join(actors)
        conditions.append(f"actor IN ('{actor_list}')")
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT 
            event_id,
            event_type,
            actor,
            target_server_id,
            action,
            outcome,
            details_json,
            timestamp
        FROM audit_log
        WHERE {where_clause}
        ORDER BY timestamp DESC
    """
    
    results = ws_query(sql, params)
    if results is None:
        results = []
    
    output = io.StringIO()
    fieldnames = [
        "event_id",
        "event_type",
        "actor",
        "target_server_id",
        "action",
        "outcome",
        "details_json",
        "timestamp"
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    
    for row in results:
        try:
            if isinstance(row, (list, tuple)):
                event = {
                    "event_id": row[0],
                    "event_type": row[1],
                    "actor": row[2],
                    "target_server_id": row[3],
                    "action": row[4],
                    "outcome": row[5],
                    "details_json": row[6],
                    "timestamp": row[7]
                }
            else:
                event = row
            
            event_row = {
                "event_id": event.get("event_id", ""),
                "event_type": event.get("event_type", ""),
                "actor": event.get("actor", ""),
                "target_server_id": event.get("target_server_id", "") or "",
                "action": event.get("action", ""),
                "outcome": event.get("outcome", ""),
                "details_json": event.get("details_json", ""),
                "timestamp": event.get("timestamp", "")
            }
            writer.writerow(event_row)
        except Exception as e:
            log.error(f"Error writing CSV row: {e}")
            continue
    
    csv_content = output.getvalue()
    output.close()
    
    log.info(f"Exported audit CSV: {len(csv_content)} bytes")
    return csv_content

def get_event_statistics(start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Get statistics about audit events.
    
    Args:
        start_date: ISO format start date
        end_date: ISO format end date
    
    Returns:
        Dict with event counts by type, actor, and outcome
    """
    where_clause = "1=1"
    if start_date:
        where_clause += f" AND timestamp >= '{start_date}'"
    if end_date:
        where_clause += f" AND timestamp <= '{end_date}'"
    
    type_sql = f"""
        SELECT event_type, COUNT(*) as cnt
        FROM audit_log
        WHERE {where_clause}
        GROUP BY event_type
        ORDER BY cnt DESC
    """
    actor_sql = f"""
        SELECT actor, COUNT(*) as cnt
        FROM audit_log
        WHERE {where_clause}
        GROUP BY actor
        ORDER BY cnt DESC
    """
    outcome_sql = f"""
        SELECT outcome, COUNT(*) as cnt
        FROM audit_log
        WHERE {where_clause}
        GROUP BY outcome
        ORDER BY cnt DESC
    """
    
    type_results = ws_query(type_sql) or []
    actor_results = ws_query(actor_sql) or []
    outcome_results = ws_query(outcome_sql) or []
    
    def extract_counts(results):
        counts = {}
        for row in results:
            if isinstance(row, (list, tuple)):
                counts[row[0]] = row[1]
            else:
                counts[row.get("event_type", row.get("actor", row.get("outcome")))] = row.get("cnt")
        return counts
    
    return {
        "by_event_type": extract_counts(type_results),
        "by_actor": extract_counts(actor_results),
        "by_outcome": extract_counts(outcome_results),
        "total_events": sum(extract_counts(type_results).values())
    }

if __name__ == "__main__":
    ensure_audit_table()
    print("Audit trail module initialized")
    print(f"Service URL: {WRITE_SERVICE_URL}")
    
    test_event_id = record_event(
        event_type="system",
        actor="audit_trail_init",
        target_server_id=None,
        action="module_initialized",
        outcome="success",
        details={"version": "1.0.0"}
    )
    print(f"Test event recorded: {test_event_id}")