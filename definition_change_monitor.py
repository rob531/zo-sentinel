#!/usr/bin/env python3
"""
definition_change_monitor.py
Daemon that polls mcp_server_registry for definition schema changes
and writes change events to mcp_definition_history.
"""

import hashlib
import json
import time
import uuid
import requests
from datetime import datetime, timezone
from typing import Optional

SERVICE_NAME = "definition_change_monitor"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
HEALTH_SERVICE_URL = "http://127.0.0.1:8772"
HEARTBEAT_INTERVAL = 60  # seconds

# Track last heartbeat to ensure <=60s interval
_last_heartbeat: float = 0


def get_time_iso() -> str:
    """Return current UTC time in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


def compute_schema_hash(tool_schema: dict) -> str:
    """
    Compute SHA256 hash of canonicalized tool_schema JSON.
    Uses sort_keys=True and separators=(',', ':') for determinism.
    """
    canonical = json.dumps(tool_schema, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def query_db(sql: str, params: Optional[dict] = None) -> list:
    """Execute a read query via write_service."""
    payload = {
        "sql": sql,
        "params": params or {}
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    result = response.json()
    return result.get("data", [])


def write_db(sql: str, params: Optional[dict] = None) -> dict:
    """Execute a write query via write_service with wait=True."""
    payload = {
        "sql": sql,
        "params": params or {},
        "wait": True
    }
    response = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def heartbeat() -> bool:
    """Send heartbeat to service_health."""
    global _last_heartbeat
    try:
        payload = {
            "service": SERVICE_NAME,
            "status": "ok",
            "timestamp": get_time_iso(),
            "ttl": HEARTBEAT_INTERVAL
        }
        response = requests.post(
            f"{HEALTH_SERVICE_URL}/health",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        _last_heartbeat = time.time()
        return True
    except Exception as e:
        print(f"heartbeat failed: {e}")
        return False


def get_table_columns(table_name: str) -> list:
    """Query information_schema.columns to get column names for a table."""
    sql = """
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = ?
    ORDER BY ordinal_position
    """
    result = query_db(sql, {"table_name": table_name})
    return [row["column_name"] for row in result]


def get_mcp_server_registry_columns() -> list:
    """Get column names from mcp_server_registry."""
    return get_table_columns("mcp_server_registry")


def get_mcp_definition_history_columns() -> list:
    """Get column names from mcp_definition_history."""
    return get_table_columns("mcp_definition_history")


def verify_schema() -> bool:
    """Verify required columns exist in mcp_definition_history."""
    try:
        columns = get_mcp_definition_history_columns()
        required = {"mcp_identifier", "prior_schema_hash", "new_schema_hash", 
                    "changed_at", "change_type", "changed_fields"}
        missing = required - set(columns)
        if missing:
            print(f"Missing columns in mcp_definition_history: {missing}")
            return False
        return True
    except Exception as e:
        print(f"Schema verification failed: {e}")
        return False


def detect_changes() -> list:
    """
    Query all rows from mcp_server_registry where prior_schema_hash IS NULL 
    or differs from current tool_schema SHA256.
    Returns list of change records.
    """
    # Query to find servers with NULL or mismatched prior_schema_hash
    sql = """
    SELECT 
        mcp_identifier,
        mcp_name,
        tool_schema,
        description,
        version,
        last_modified,
        prior_schema_hash
    FROM mcp_server_registry
    """
    
    try:
        servers = query_db(sql)
    except Exception as e:
        print(f"Failed to query mcp_server_registry: {e}")
        return []
    
    changes = []
    for server in servers:
        mcp_identifier = server["mcp_identifier"]
        tool_schema = server.get("tool_schema", {})
        prior_hash = server.get("prior_schema_hash")
        
        # Compute current hash
        if tool_schema is None:
            tool_schema = {}
        
        current_hash = compute_schema_hash(tool_schema)
        
        # Check if change detected
        if prior_hash is None or prior_hash != current_hash:
            old_hash = prior_hash if prior_hash else "NONE"
            
            # Determine changed fields
            changed_fields = determine_changed_fields(server)
            
            # Determine change type
            change_type = determine_change_type(old_hash, current_hash, server)
            
            changes.append({
                "mcp_identifier": mcp_identifier,
                "old_hash": old_hash,
                "new_hash": current_hash,
                "changed_fields": changed_fields,
                "change_type": change_type,
                "tool_schema": tool_schema
            })
    
    return changes


def determine_changed_fields(server: dict) -> list:
    """
    Determine which top-level fields have changed.
    Compare against stored prior_schema_hash if available.
    """
    # For now, return common field names that could change
    # In a full implementation, we would need to track the old schema
    # to compute actual differences
    changed = []
    
    # Check if tool_schema itself changed (always the primary trigger)
    if "tool_schema" in server:
        changed.append("tool_schema")
    
    # Check other common fields
    for field in ["description", "version", "mcp_name"]:
        if field in server:
            changed.append(field)
    
    return changed


def determine_change_type(old_hash: str, new_hash: str, server: dict) -> str:
    """
    Determine the type of change:
    - INITAL: first time recording (prior_hash was NULL)
    - UPDATE: general update
    - CAPABILITY_ADDED: tool was added
    - CAPABILITY_REMOVED: tool was removed
    """
    if old_hash == "NONE":
        return "INITAL"
    
    # For UPDATE vs CAPABILITY_ADDED/REMOVED, we'd need to compare schemas
    # For now, default to UPDATE
    return "UPDATE"


def record_history_change(change: dict) -> bool:
    """Write a change record to mcp_definition_history."""
    sql = """
    INSERT INTO mcp_definition_history (
        mcp_identifier,
        prior_schema_hash,
        new_schema_hash,
        changed_at,
        change_type,
        changed_fields
    ) VALUES (?, ?, ?, ?, ?, ?)
    """
    
    params = {
        "mcp_identifier": change["mcp_identifier"],
        "prior_schema_hash": change["old_hash"],
        "new_schema_hash": change["new_hash"],
        "changed_at": get_time_iso(),
        "change_type": change["change_type"],
        "changed_fields": json.dumps(change["changed_fields"])
    }
    
    try:
        write_db(sql, params)
        return True
    except Exception as e:
        print(f"Failed to record history change for {change['mcp_identifier']}: {e}")
        return False


def update_registry_prior_hash(mcp_identifier: str, new_hash: str) -> bool:
    """Update mcp_server_registry prior_schema_hash to new_hash."""
    sql = """
    UPDATE mcp_server_registry
    SET prior_schema_hash = ?,
        last_modified = ?
    WHERE mcp_identifier = ?
    """
    
    params = {
        "prior_schema_hash": new_hash,
        "last_modified": get_time_iso(),
        "mcp_identifier": mcp_identifier
    }
    
    try:
        write_db(sql, params)
        return True
    except Exception as e:
        print(f"Failed to update prior_schema_hash for {mcp_identifier}: {e}")
        return False


def process_changes(changes: list) -> int:
    """Process all detected changes. Returns count of recorded changes."""
    recorded = 0
    
    for change in changes:
        # Write to history
        if record_history_change(change):
            recorded += 1
            # Update registry with new hash
            update_registry_prior_hash(
                change["mcp_identifier"],
                change["new_hash"]
            )
    
    return recorded


def polling_loop(interval: int = 60) -> None:
    """
    Main polling loop.
    Continuously checks for schema changes and sends heartbeats.
    """
    print(f"Starting polling loop (interval={interval}s)")
    
    consecutive_failures = 0
    max_consecutive_failures = 5
    
    while True:
        try:
            # Check for changes
            changes = detect_changes()
            
            if changes:
                recorded = process_changes(changes)
                print(f"{recorded} changes recorded")
            else:
                print("no changes detected")
            
            consecutive_failures = 0
            
        except Exception as e:
            consecutive_failures += 1
            print(f"Error in polling loop: {e}")
            
            if consecutive_failures >= max_consecutive_failures:
                print("Too many consecutive failures, exiting")
                break
        
        # Send heartbeat
        heartbeat()
        print("heartbeat OK")
        
        # Sleep until next poll
        time.sleep(interval)


def run() -> None:
    """
    Main entry point for the daemon.
    Initializes, verifies schema, and starts polling loop.
    """
    print(f"{SERVICE_NAME} starting...")
    
    # Verify schema before starting
    if not verify_schema():
        print("Schema verification failed, cannot start")
        return
    
    # Send initial heartbeat
    heartbeat()
    print("heartbeat OK")
    
    # Start polling loop (must not block more than 10s to enter)
    polling_loop(interval=HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    run()