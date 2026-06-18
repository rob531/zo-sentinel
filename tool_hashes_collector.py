"""
tool_hashes_collector.py - Pure daemon module that collects SHA-256 tool hashes from the MCP registry
and writes fingerprint rows to the mcp_tool_hashes table.

Consumed by aidr_commit_gateway and dependency_chain_auditor for commit-enforcement decisions.
"""

import hashlib
import json
import threading
import time
from typing import Optional

import requests

# Service endpoints
WRITE_SERVICE_URL = "http://localhost:8080"
HEALTH_SERVICE_URL = "http://localhost:8081/health"


def compute_tool_hash(tool_name: str, input_schema: dict) -> str:
    """
    Compute a stable SHA-256 fingerprint of a tool's input schema.
    
    Args:
        tool_name: Name of the tool
        input_schema: JSON schema dict for the tool's input parameters
        
    Returns:
        SHA-256 hex digest (64 characters)
    """
    # Canonical representation for stable hashing
    canonical = {
        "tool_name": tool_name,
        "schema": input_schema
    }
    
    # Sort keys for determinism across runs
    serialized = json.dumps(canonical, sort_keys=True, separators=(',', ':'))
    
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def _heartbeat() -> None:
    """
    Internal function that sends heartbeat POST to service_health every 60 seconds.
    Runs in a separate thread from the main collection loop.
    """
    while True:
        try:
            requests.post(
                HEALTH_SERVICE_URL,
                json={"service": "tool_hashes_collector", "status": "alive"},
                timeout=5
            )
        except requests.RequestException:
            pass  # Swallow network errors to keep heartbeat running
        time.sleep(60)


def _query_registry() -> list:
    """
    Query the mcp_server_registry for servers with tool schemas.
    
    Returns:
        List of dicts with mcp_name and tool_schema keys
    """
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={
            "sql": "SELECT mcp_name, tool_schema FROM mcp_server_registry WHERE tool_schema IS NOT NULL"
        },
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def _write_hash(mcp_name: str, tool_name: str, schema_hash: str) -> None:
    """
    Write a single tool hash row to the mcp_tool_hashes table.
    
    Args:
        mcp_name: Name of the MCP server
        tool_name: Name of the tool
        schema_hash: SHA-256 hash of the tool's input schema
    """
    requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json={
            "table": "mcp_tool_hashes",
            "rows": [{
                "mcp_name": mcp_name,
                "tool_name": tool_name,
                "schema_hash": schema_hash,
                "collected_at": time.time()
            }]
        },
        timeout=30
    )


def _process_server(server: dict) -> list:
    """
    Extract individual tool hashes from a server's tool_schema.
    
    Args:
        server: Dict with mcp_name and tool_schema (list of tool definitions)
        
    Returns:
        List of (tool_name, schema_hash) tuples
    """
    results = []
    mcp_name = server["mcp_name"]
    tool_schema = server.get("tool_schema") or []
    
    for tool_def in tool_schema:
        if isinstance(tool_def, dict) and "name" in tool_def:
            tool_name = tool_def["name"]
            input_schema = tool_def.get("input_schema", {})
            schema_hash = compute_tool_hash(tool_name, input_schema)
            results.append((mcp_name, tool_name, schema_hash))
    
    return results


def run() -> None:
    """
    Main daemon entry point.
    Collects tool hashes from registry and writes to mcp_tool_hashes table.
    Runs indefinitely with 60-second heartbeat interval.
    """
    # Start heartbeat in background thread - fires independently of collection cycle
    heartbeat_thread = threading.Thread(target=_heartbeat, daemon=True)
    heartbeat_thread.start()
    
    while True:
        try:
            # Query registry for MCP servers with tool schemas
            servers = _query_registry()
            
            # Process each server and write hashes
            for server in servers:
                for mcp_name, tool_name, schema_hash in _process_server(server):
                    _write_hash(mcp_name, tool_name, schema_hash)
                    
        except Exception:
            # Continue running even if collection cycle raises
            # Heartbeat continues independently in its thread
            pass
        
        # Sleep between collection cycles
        time.sleep(60)


if __name__ == "__main__":
    result = compute_tool_hash('list_files', {'type': 'object', 'properties': {'path': {'type': 'string'}}})
    assert len(result) == 64, f"Expected 64 chars, got {len(result)}"
    assert result.isalnum(), f"Expected alphanumeric, got {result}"
    print("PASS")