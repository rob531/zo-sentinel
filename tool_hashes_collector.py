"""
tool_hashes_collector.py

Pure daemon module that collects SHA-256 tool hashes from the MCP registry
and writes fingerprint rows to the mcp_tool_hashes table.
"""

import hashlib
import json
import time
import threading
from datetime import datetime, timezone
from typing import Optional

import requests


def compute_tool_hash(tool_name: str, input_schema: dict) -> str:
    """
    Compute a stable SHA-256 fingerprint of a tool's schema.
    
    Args:
        tool_name: Name of the tool
        input_schema: JSON schema of tool's input parameters
        
    Returns:
        64-character hexadecimal SHA-256 hash
    """
    # Canonicalize by sorting keys and using stable JSON encoding
    canonical = json.dumps(
        {"tool_name": tool_name, "input_schema": input_schema},
        sort_keys=True,
        separators=(',', ':')
    )
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def _heartbeat():
    """
    Internal heartbeat that POSTs to service_health every 60 seconds.
    Continues firing even if collection cycles fail.
    """
    def _send_heartbeat():
        while True:
            try:
                requests.post(
                    "http://localhost:8080/service_health",
                    json={"component": "tool_hashes_collector", "status": "running"},
                    timeout=5
                )
            except requests.RequestException:
                pass  # Heartbeat failures are silent
            time.sleep(60)
    
    thread = threading.Thread(target=_send_heartbeat, daemon=True)
    thread.start()


def _query_registry(sql: str) -> list:
    """
    Query the MCP server registry via write_service.
    
    Args:
        sql: SQL query to execute
        
    Returns:
        List of result rows as dicts
    """
    response = requests.post(
        "http://localhost:8080/write_service/query",
        json={"sql": sql},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def _write_tool_hashes(rows: list) -> None:
    """
    Write tool hash rows to mcp_tool_hashes table via write_service.
    
    Args:
        rows: List of row dicts with keys: mcp_name, tool_name, schema_hash, collected_at
    """
    response = requests.post(
        "http://localhost:8080/write_service/write",
        json={"table": "mcp_tool_hashes", "rows": rows},
        timeout=30
    )
    response.raise_for_status()


def run() -> None:
    """
    Main daemon entry point.
    
    Reads mcp_server_registry via write_service /query,
    computes SHA-256 fingerprints for each tool schema,
    and writes results to mcp_tool_hashes via write_service /write.
    
    Heartbeat fires every 60s regardless of collection cycle status.
    """
    _heartbeat()
    
    while True:
        try:
            # Query registry for MCP servers with tool schemas
            results = _query_registry(
                "SELECT mcp_name, tool_schema FROM mcp_server_registry WHERE tool_schema IS NOT NULL"
            )
            
            rows = []
            collected_at = datetime.now(timezone.utc).isoformat()
            
            for row in results:
                mcp_name = row["mcp_name"]
                tool_schema = json.loads(row["tool_schema"])
                
                # Iterate over tools in the schema
                for tool_name, input_schema in tool_schema.items():
                    schema_hash = compute_tool_hash(tool_name, input_schema)
                    rows.append({
                        "mcp_name": mcp_name,
                        "tool_name": tool_name,
                        "schema_hash": schema_hash,
                        "collected_at": collected_at
                    })
            
            # Write results if any
            if rows:
                _write_tool_hashes(rows)
        
        except Exception:
            # Collection cycle failed - heartbeat continues independently
            pass
        
        # Sleep between collection cycles
        time.sleep(60)


if __name__ == '__main__':
    # Acceptance test
    result = compute_tool_hash(
        'list_files',
        {'type': 'object', 'properties': {'path': {'type': 'string'}}}
    )
    assert len(result) == 64, f"Expected 64 chars, got {len(result)}"
    assert result.isalnum(), f"Expected alphanumeric, got {result}"
    print("PASS")