#!/usr/bin/env python3
"""
MCP Definition History Writer Daemon

Pure daemon that detects definition changes for MCP servers and writes snapshot rows
to mcp_definition_history via write_service (:8772).

PURPOSE: track version history of MCP definitions over time for audit and rollback.
"""

import json
import time
import hashlib
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any


WRITE_SERVICE_URL = "http://127.0.0.1:8772"
WRITE_ENDPOINT = f"{WRITE_SERVICE_URL}/write"
QUERY_ENDPOINT = f"{WRITE_SERVICE_URL}/query"
HEARTBEAT_INTERVAL = 300  # seconds


def get_current_timestamp() -> str:
    """Get current timestamp in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


def compute_definition_hash(definition: Any) -> str:
    """Compute SHA256 hash of a definition."""
    definition_str = json.dumps(definition, sort_keys=True)
    return hashlib.sha256(definition_str.encode()).hexdigest()


def fetch_current_definitions() -> Dict[str, Any]:
    """Fetch current definitions from mcp_server_registry."""
    try:
        payload = {
            "table": "mcp_server_registry",
            "columns": ["server_id", "definition", "last_definition_hash"]
        }
        response = requests.post(QUERY_ENDPOINT, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        servers = result.get("rows", result) if isinstance(result, dict) else result
        return {row["server_id"]: row for row in servers}
    except requests.RequestException as e:
        print(f"Error fetching server registry: {e}")
        return {}


def fetch_last_snapshot_from_history(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the last snapshot for a server from mcp_definition_history."""
    try:
        payload = {
            "table": "mcp_definition_history",
            "columns": ["server_id", "definition_snapshot", "changed_at", "changed_by", "change_reason"],
            "filter": {"server_id": server_id},
            "order_by": "changed_at",
            "order": "desc",
            "limit": 1
        }
        response = requests.post(QUERY_ENDPOINT, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        rows = result.get("rows", result) if isinstance(result, dict) else result
        return rows[0] if rows else None
    except requests.RequestException as e:
        print(f"Error fetching last snapshot: {e}")
        return None


def check_for_changes() -> list[dict]:
    """Check for definition changes across all MCP servers."""
    changes = []
    
    current_servers = fetch_current_definitions()
    
    for server_id, server_data in current_servers.items():
        current_def = server_data.get("definition")
        current_hash = compute_definition_hash(current_def)
        last_snapshot = fetch_last_snapshot_from_history(server_id)
        
        if last_snapshot is None:
            changes.append({
                "server_id": server_id,
                "old_def": None,
                "new_def": current_def,
                "changed_by": "system",
                "change_reason": "new_server"
            })
        else:
            last_def = last_snapshot.get("definition_snapshot")
            last_hash = compute_definition_hash(last_def)
            if current_hash != last_hash:
                changes.append({
                    "server_id": server_id,
                    "old_def": last_def,
                    "new_def": current_def,
                    "changed_by": "system",
                    "change_reason": "definition_updated"
                })
    
    return changes


def write_snapshot(server_id: str, old_def: Optional[Any], new_def: Any, changed_by: str) -> None:
    """Write a snapshot row to mcp_definition_history."""
    payload = {
        "table": "mcp_definition_history",
        "rows": [{
            "server_id": server_id,
            "definition_snapshot": new_def,
            "changed_at": get_current_timestamp(),
            "changed_by": changed_by,
            "change_reason": "definition_change"
        }],
        "wait": True
    }
    response = requests.post(WRITE_ENDPOINT, json=payload, timeout=30)
    response.raise_for_status()


def send_heartbeat() -> None:
    """Send heartbeat to service_health table."""
    payload = {
        "table": "service_health",
        "rows": [{
            "service": "mcp_definition_history_writer",
            "last_heartbeat": get_current_timestamp()
        }],
        "wait": True
    }
    response = requests.post(WRITE_ENDPOINT, json=payload, timeout=30)
    response.raise_for_status()


def run() -> None:
    """Main daemon loop that runs every 300 seconds."""
    while True:
        try:
            send_heartbeat()
            changes = check_for_changes()
            for change in changes:
                write_snapshot(
                    change["server_id"],
                    change["old_def"],
                    change["new_def"],
                    change["changed_by"]
                )
        except requests.RequestException:
            pass
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    send_heartbeat()
    print("mcp_definition_history_writer heartbeat OK")