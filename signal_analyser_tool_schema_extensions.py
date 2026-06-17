#!/usr/bin/env python3
"""
signal_analyser_tool_schema_extensions.py

Companion module extending signal_analyser with mcp_tool_schema_patterns detection.
Reads MCP tool definitions from mcp_fingerprints or mcp_tool_hashes,
classifies progressive-disclosure vs brute-force enumeration patterns,
and writes results to mcp_signal_enrichments.
"""

import json
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

import requests

# Import the pure mcp_tool_schema_patterns library
from mcp_tool_schema_patterns import classify_tool_schema

# Configuration
WRITE_SERVICE_HOST = "127.0.0.1"
WRITE_SERVICE_PORT = 8772
WRITE_SERVICE_URL = f"http://{WRITE_SERVICE_HOST}:{WRITE_SERVICE_PORT}"
REQUEST_TIMEOUT = 10
HEARTBEAT_INTERVAL = 60  # seconds

# Global state
_last_heartbeat = None
_heartbeat_lock = threading.Lock()


def _make_request(method: str, endpoint: str, data: Optional[dict] = None, retries: int = 3) -> dict:
    """
    Make HTTP request to write_service with exponential backoff on 5xx errors.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        endpoint: API endpoint path
        data: Optional JSON payload
        retries: Maximum retry attempts
        
    Returns:
        Response data as dict
        
    Raises:
        requests.RequestException: On failure after retries exhausted
    """
    url = f"{WRITE_SERVICE_URL}{endpoint}"
    headers = {"Content-Type": "application/json"}
    
    backoff = 1.0  # Start with 1 second backoff
    
    for attempt in range(retries + 1):
        try:
            response = requests.request(
                method=method,
                url=url,
                json=data,
                headers=headers,
                timeout=REQUEST_TIMEOUT
            )
            
            # Success
            if 200 <= response.status_code < 300:
                return response.json() if response.content else {}
            
            # Server error - retry with backoff
            if 500 <= response.status_code < 600:
                if attempt < retries:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                    
            # Client error or exhausted retries
            response.raise_for_status()
            
        except requests.exceptions.Timeout:
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except requests.exceptions.ConnectionError:
            if attempt < retries:
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
        except requests.RequestException:
            raise
    
    return {}


def _heartbeat() -> None:
    """
    Send heartbeat to service_health endpoint.
    Must be called at least every 60 seconds.
    """
    global _last_heartbeat
    
    try:
        payload = {
            "service": "signal_analyser_tool_schema_extensions",
            "timestamp": datetime.utcnow().isoformat(),
            "status": "running"
        }
        _make_request("POST", "/service_health", data=payload)
        
        with _heartbeat_lock:
            _last_heartbeat = datetime.utcnow()
    except Exception as e:
        # Log but don't fail - heartbeat is best-effort
        print(f"Warning: Heartbeat failed: {e}")


def _should_send_heartbeat() -> bool:
    """Check if enough time has passed to warrant a new heartbeat."""
    with _heartbeat_lock:
        if _last_heartbeat is None:
            return True
        elapsed = (datetime.utcnow() - _last_heartbeat).total_seconds()
        return elapsed >= HEARTBEAT_INTERVAL


def _classify_server_tools(tools: list) -> dict:
    """
    Classify server tools using mcp_tool_schema_patterns.
    
    This is a passthrough to the mcp_tool_schema_patterns library.
    
    Args:
        tools: List of tool definitions (each with 'name', 'description', etc.)
        
    Returns:
        Classification result dict with pattern, confidence, evidence, etc.
    """
    return classify_tool_schema(tools)


def _get_mcp_fingerprints() -> list:
    """
    Fetch MCP fingerprints from the database.
    
    Returns:
        List of fingerprint records with tool_definitions
    """
    query = {
        "table": "mcp_fingerprints",
        "columns": ["server_id", "mcp_name", "registry_source", "tool_definitions", "updated_at"]
    }
    
    result = _make_request("POST", "/query", data=query)
    return result.get("data", [])


def _get_mcp_tool_hashes() -> list:
    """
    Fetch MCP tool hashes from the database.
    
    Returns:
        List of tool hash records with tools field
    """
    query = {
        "table": "mcp_tool_hashes",
        "columns": ["server_id", "mcp_name", "tools", "updated_at"]
    }
    
    result = _make_request("POST", "/query", data=query)
    return result.get("data", [])


def _get_existing_signals(server_id: str) -> list:
    """
    Check for existing tool_schema_pattern signals for a server.
    
    Args:
        server_id: The server identifier
        
    Returns:
        List of existing signal records
    """
    query = {
        "table": "mcp_signal_enrichments",
        "columns": ["id", "signal_type"],
        "filters": {
            "server_id": server_id,
            "signal_type": "tool_schema_pattern"
        }
    }
    
    result = _make_request("POST", "/query", data=query)
    return result.get("data", [])


def _write_enrichment(server_id: str, mcp_name: str, pattern: str, 
                      tool_count: int, evidence: dict) -> bool:
    """
    Write enrichment record to mcp_signal_enrichments.
    
    Args:
        server_id: The server identifier
        mcp_name: Name of the MCP server
        pattern: Classification pattern
        tool_count: Number of tools analyzed
        evidence: Detailed evidence blob
        
    Returns:
        True if write successful, False otherwise
    """
    evidence_blob = {
        "pattern": pattern,
        "tool_count": tool_count,
        "evidence": evidence,
        "analyzed_at": datetime.utcnow().isoformat()
    }
    
    record = {
        "table": "mcp_signal_enrichments",
        "data": {
            "server_id": server_id,
            "mcp_name": mcp_name,
            "signal_type": "tool_schema_pattern",
            "evidence_blob": json.dumps(evidence_blob),
            "created_at": datetime.utcnow().isoformat()
        }
    }
    
    try:
        result = _make_request("POST", "/insert", data=record)
        return result.get("success", False) or result.get("affected_rows", 0) > 0
    except Exception:
        return False


def _process_fingerprint_record(record: dict) -> bool:
    """
    Process a single mcp_fingerprints record.
    
    Args:
        record: Fingerprint record with tool_definitions
        
    Returns:
        True if processing succeeded, False otherwise
    """
    server_id = record.get("server_id")
    mcp_name = record.get("mcp_name", "unknown")
    registry_source = record.get("registry_source", "unknown")
    
    if not server_id:
        return False
    
    # Check for existing signal (idempotency)
    existing = _get_existing_signals(server_id)
    if existing:
        return True  # Already processed, consider it success
    
    # Parse tool definitions
    tool_defs_raw = record.get("tool_definitions")
    if not tool_defs_raw:
        return False
    
    try:
        if isinstance(tool_defs_raw, str):
            tools = json.loads(tool_defs_raw)
        elif isinstance(tool_defs_raw, list):
            tools = tool_defs_raw
        else:
            return False
    except (json.JSONDecodeError, TypeError):
        return False
    
    if not tools:
        return False
    
    # Classify the tools
    classification = _classify_server_tools(tools)
    pattern = classification.get("pattern", "unknown")
    evidence = classification.get("evidence", {})
    tool_count = classification.get("tool_count", len(tools))
    
    # Write enrichment
    return _write_enrichment(server_id, mcp_name, pattern, tool_count, evidence)


def _process_tool_hash_record(record: dict) -> bool:
    """
    Process a single mcp_tool_hashes record.
    
    Args:
        record: Tool hash record with tools field
        
    Returns:
        True if processing succeeded, False otherwise
    """
    server_id = record.get("server_id")
    mcp_name = record.get("mcp_name", "unknown")
    
    if not server_id:
        return False
    
    # Check for existing signal (idempotency)
    existing = _get_existing_signals(server_id)
    if existing:
        return True  # Already processed
    
    # Parse tools
    tools_raw = record.get("tools")
    if not tools_raw:
        return False
    
    try:
        if isinstance(tools_raw, str):
            tools = json.loads(tools_raw)
        elif isinstance(tools_raw, list):
            tools = tools_raw
        else:
            return False
    except (json.JSONDecodeError, TypeError):
        return False
    
    if not tools:
        return False
    
    # Classify the tools
    classification = _classify_server_tools(tools)
    pattern = classification.get("pattern", "unknown")
    evidence = classification.get("evidence", {})
    tool_count = classification.get("tool_count", len(tools))
    
    # Write enrichment
    return _write_enrichment(server_id, mcp_name, pattern, tool_count, evidence)


def _process_batch(records: list, source: str) -> int:
    """
    Process a batch of records.
    
    Args:
        records: List of records to process
        source: Source table name for logging
        
    Returns:
        Number of successfully processed records
    """
    processed = 0
    
    for record in records:
        try:
            if source == "mcp_fingerprints":
                success = _process_fingerprint_record(record)
            elif source == "mcp_tool_hashes":
                success = _process_tool_hash_record(record)
            else:
                continue
            
            if success:
                processed += 1
                
            # Send heartbeat periodically
            if _should_send_heartbeat():
                _heartbeat()
                
        except Exception as e:
            print(f"Error processing record from {source}: {e}")
            continue
    
    return processed


def run(batch_size: int = 100) -> int:
    """
    Main entry point - process all MCP servers in batches.
    
    Reads MCP tool definitions from mcp_fingerprints or mcp_tool_hashes,
    classifies patterns using mcp_tool_schema_patterns, and writes
    results to mcp_signal_enrichments.
    
    Args:
        batch_size: Number of records to process per batch
        
    Returns:
        Total number of records processed across all sources
    """
    total_processed = 0
    
    # Initial heartbeat
    _heartbeat()
    
    # Process mcp_fingerprints
    try:
        fingerprints = _get_mcp_fingerprints()
        
        for i in range(0, len(fingerprints), batch_size):
            batch = fingerprints[i:i + batch_size]
            processed = _process_batch(batch, "mcp_fingerprints")
            total_processed += processed
            print(f"Fingerprints batch {i // batch_size + 1}: processed {processed}")
            
    except Exception as e:
        print(f"Error processing mcp_fingerprints: {e}")
    
    # Process mcp_tool_hashes
    try:
        tool_hashes = _get_mcp_tool_hashes()
        
        for i in range(0, len(tool_hashes), batch_size):
            batch = tool_hashes[i:i + batch_size]
            processed = _process_batch(batch, "mcp_tool_hashes")
            total_processed += processed
            print(f"Tool hashes batch {i // batch_size + 1}: processed {processed}")
            
    except Exception as e:
        print(f"Error processing mcp_tool_hashes: {e}")
    
    # Final heartbeat
    _heartbeat()
    
    print(f"Total records processed: {total_processed}")
    return total_processed


if __name__ == "__main__":
    # Smoke test: verify mcp_tool_schema_patterns classification
    print("Running smoke test...")
    result = classify_tool_schema([{'name': 'cmd', 'description': 'run command'}] * 3)
    assert result['pattern'] in ('progressive_disclosure', 'brute_force_enumeration', 'hybrid'), \
        f"Unexpected pattern: {result['pattern']}"
    print(f"PASS: tool_schema_patterns classification works (pattern={result['pattern']})")
    
    # Run against sample servers
    print("\nRunning against sample servers...")
    run()