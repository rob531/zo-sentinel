#!/usr/bin/env python3
"""
signal_analyser_tool_schema_extensions.py

Companion module extending signal_analyser with mcp_tool_schema_patterns detection.
Reads MCP tool definitions, classifies progressive-disclosure vs brute-force enumeration
patterns, and writes results to mcp_signal_enrichments.

This is a pure companion module — signal_analyser.py is protected and cannot be edited directly.
"""

import json
import time
import logging
import random
from datetime import datetime, timedelta
from typing import Optional

import requests

# Configuration
WRITE_SERVICE_HOST = "127.0.0.1"
WRITE_SERVICE_PORT = 8772
WRITE_SERVICE_TIMEOUT = 10  # seconds
HEALTH_CHECK_INTERVAL = 60  # seconds
MAX_RETRIES = 5
BASE_BACKOFF_DELAY = 1.0

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WriteServiceClient:
    """Client for write_service with exponential backoff on 5xx errors."""
    
    def __init__(self, host: str = WRITE_SERVICE_HOST, port: int = WRITE_SERVICE_PORT):
        self.base_url = f"http://{host}:{port}"
        self.timeout = WRITE_SERVICE_TIMEOUT
    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter."""
        delay = BASE_BACKOFF_DELAY * (2 ** attempt)
        jitter = delay * 0.1 * random.random()
        return min(delay + jitter, 60.0)
    
    def _make_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """Make HTTP request with exponential backoff on 5xx errors."""
        url = f"{self.base_url}{endpoint}"
        
        for attempt in range(MAX_RETRIES):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    json=data,
                    timeout=self.timeout
                )
                
                if 200 <= response.status_code < 500:
                    response.raise_for_status()
                    return response.json() if response.content else {}
                
                if 500 <= response.status_code < 600:
                    logger.warning(
                        f"Attempt {attempt + 1}/{MAX_RETRIES}: "
                        f"Server error {response.status_code}"
                    )
                    if attempt < MAX_RETRIES - 1:
                        delay = self._calculate_backoff_delay(attempt)
                        logger.info(f"Retrying in {delay:.1f}s...")
                        time.sleep(delay)
                        continue
                    else:
                        raise requests.exceptions.HTTPError(
                            f"Server error {response.status_code} after {MAX_RETRIES} attempts"
                        )
                        
            except requests.exceptions.Timeout:
                logger.warning(f"Attempt {attempt + 1}/{MAX_RETRIES}: Request timeout")
                if attempt < MAX_RETRIES - 1:
                    delay = self._calculate_backoff_delay(attempt)
                    logger.info(f"Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                else:
                    raise
                    
            except requests.exceptions.RequestException as e:
                logger.warning(f"Attempt {attempt + 1}/{MAX_RETRIES}: {e}")
                if attempt < MAX_RETRIES - 1:
                    delay = self._calculate_backoff_delay(attempt)
                    logger.info(f"Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                    continue
                else:
                    raise
        
        return {}
    
    def query(self, sql: str, params: Optional[dict] = None) -> list:
        """Execute SELECT query against write_service."""
        data = {"sql": sql, "params": params or {}}
        result = self._make_request("POST", "/query", data)
        return result.get("data", [])
    
    def execute(self, sql: str, params: Optional[dict] = None) -> dict:
        """Execute non-SELECT query (INSERT/UPDATE/DELETE) against write_service."""
        data = {"sql": sql, "params": params or {}}
        return self._make_request("POST", "/execute", data)


class HealthMonitor:
    """Reports heartbeat to service_health at regular intervals."""
    
    def __init__(self, client: WriteServiceClient, interval: int = HEALTH_CHECK_INTERVAL):
        self.client = client
        self.interval = interval
        self.last_heartbeat: Optional[datetime] = None
    
    def heartbeat(self, processed_count: int = 0, is_final: bool = False) -> None:
        """Send heartbeat to service_health."""
        now = datetime.utcnow()
        
        if self.last_heartbeat and (now - self.last_heartbeat).total_seconds() < self.interval and not is_final:
            return
        
        sql = """
            INSERT INTO service_health (service_name, status, last_heartbeat, details)
            VALUES (?, ?, ?, ?)
        """
        details = {
            "processed_count": processed_count,
            "is_final": is_final
        }
        params = {
            "service_name": "signal_analyser_tool_schema_extensions",
            "status": "completed" if is_final else "running",
            "last_heartbeat": now.isoformat(),
            "details": json.dumps(details)
        }
        
        try:
            self.client.execute(sql, params)
            self.last_heartbeat = now
            logger.debug(f"Heartbeat sent: processed={processed_count}, final={is_final}")
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")


def _classify_server_tools(tools: list) -> dict:
    """
    Classify a list of tools using mcp_tool_schema_patterns.
    
    Args:
        tools: List of tool definitions (each tool should have 'name' and optionally 'description')
        
    Returns:
        Dictionary with pattern classification and evidence
    """
    try:
        from mcp_tool_schema_patterns import classify_tool_schema
        return classify_tool_schema(tools)
    except ImportError:
        logger.warning("mcp_tool_schema_patterns not available, using fallback classification")
        return _fallback_classification(tools)
    except Exception as e:
        logger.error(f"Classification error: {e}")
        return {
            "pattern": "unknown",
            "confidence": 0.0,
            "evidence": {"error": str(e)}
        }


def _fallback_classification(tools: list) -> dict:
    """Fallback classification when mcp_tool_schema_patterns is unavailable."""
    if not tools:
        return {
            "pattern": "unknown",
            "confidence": 0.0,
            "evidence": "No tools provided for classification"
        }
    
    tool_count = len(tools)
    
    # Simple heuristic based on tool count
    if tool_count <= 5:
        return {
            "pattern": "progressive_disclosure",
            "confidence": 0.6,
            "evidence": {
                "method": "count_heuristic",
                "reason": f"Small tool count ({tool_count}) suggests progressive disclosure pattern"
            }
        }
    elif tool_count <= 20:
        return {
            "pattern": "hybrid",
            "confidence": 0.5,
            "evidence": {
                "method": "count_heuristic",
                "reason": f"Moderate tool count ({tool_count}) suggests hybrid pattern"
            }
        }
    else:
        return {
            "pattern": "brute_force_enumeration",
            "confidence": 0.6,
            "evidence": {
                "method": "count_heuristic",
                "reason": f"Large tool count ({tool_count}) suggests brute force enumeration pattern"
            }
        }


def _get_mcp_tools_from_fingerprints(client: WriteServiceClient, limit: int = 100) -> list:
    """Retrieve MCP tools from mcp_fingerprints table."""
    query = """
        SELECT 
            mcp_fingerprints.server_id,
            mcp_fingerprints.mcp_name,
            mcp_fingerprints.registry_source,
            mcp_fingerprints.tool_definitions
        FROM mcp_fingerprints
        WHERE mcp_fingerprints.tool_definitions IS NOT NULL
        AND mcp_fingerprints.tool_definitions != '[]'
        AND mcp_fingerprints.tool_definitions != ''
        LIMIT ?
    """
    try:
        result = client.query(query, {"limit": limit})
        return result
    except Exception as e:
        logger.error(f"Failed to query mcp_fingerprints: {e}")
        return []


def _get_mcp_tools_from_hashes(client: WriteServiceClient, limit: int = 100) -> list:
    """Retrieve MCP tools from mcp_tool_hashes table as alternative source."""
    query = """
        SELECT 
            mcp_tool_hashes.server_id,
            mcp_tool_hashes.mcp_name,
            mcp_tool_hashes.registry_source,
            mcp_tool_hashes.tools
        FROM mcp_tool_hashes
        WHERE mcp_tool_hashes.tools IS NOT NULL
        AND mcp_tool_hashes.tools != '[]'
        AND mcp_tool_hashes.tools != ''
        LIMIT ?
    """
    try:
        result = client.query(query, {"limit": limit})
        return result
    except Exception as e:
        logger.error(f"Failed to query mcp_tool_hashes: {e}")
        return []


def _parse_tools(tool_definitions) -> list:
    """Parse tool definitions from various formats."""
    if not tool_definitions:
        return []
    
    if isinstance(tool_definitions, list):
        return tool_definitions
    
    if isinstance(tool_definitions, str):
        try:
            return json.loads(tool_definitions)
        except json.JSONDecodeError:
            logger.warning("Failed to parse tool_definitions JSON")
            return []
    
    return []


def _check_existing_enrichment(client: WriteServiceClient, server_id: str) -> bool:
    """Check if enrichment already exists for this server (idempotency check)."""
    query = """
        SELECT 1 FROM mcp_signal_enrichments
        WHERE server_id = ?
        AND signal_type = 'tool_schema_pattern'
        LIMIT 1
    """
    try:
        result = client.query(query, {"server_id": server_id})
        return len(result) > 0
    except Exception as e:
        logger.error(f"Failed to check existing enrichment: {e}")
        return False


def _write_enrichment(client: WriteServiceClient, enrichment: dict) -> bool:
    """Write enrichment record to mcp_signal_enrichments."""
    sql = """
        INSERT INTO mcp_signal_enrichments 
        (server_id, mcp_name, signal_type, evidence_blob, created_at)
        VALUES (?, ?, ?, ?, ?)
    """
    params = {
        "server_id": enrichment["server_id"],
        "mcp_name": enrichment["mcp_name"],
        "signal_type": "tool_schema_pattern",
        "evidence_blob": json.dumps(enrichment["evidence_blob"]),
        "created_at": datetime.utcnow().isoformat()
    }
    try:
        client.execute(sql, params)
        return True
    except Exception as e:
        logger.error(f"Failed to write enrichment: {e}")
        return False


def _process_server_tools(client: WriteServiceClient, server_record: dict) -> dict:
    """Process tools for a single server and create enrichment."""
    server_id = server_record["server_id"]
    mcp_name = server_record["mcp_name"]
    registry_source = server_record.get("registry_source", "unknown")
    
    # Get tools from appropriate field
    tool_definitions = server_record.get("tool_definitions") or server_record.get("tools", [])
    
    # Parse tools
    tools = _parse_tools(tool_definitions)
    
    if not tools:
        return {
            "server_id": server_id,
            "status": "skipped",
            "reason": "no_tools"
        }
    
    # Classify using mcp_tool_schema_patterns
    classification = _classify_server_tools(tools)
    
    # Build evidence blob
    evidence_blob = {
        "pattern": classification["pattern"],
        "tool_count": len(tools),
        "evidence": classification.get("evidence", {}),
        "registry_source": registry_source,
        "confidence": classification.get("confidence", 0.5)
    }
    
    # Create enrichment record
    enrichment = {
        "server_id": server_id,
        "mcp_name": mcp_name,
        "evidence_blob": evidence_blob
    }
    
    # Write to database
    success = _write_enrichment(client, enrichment)
    
    return {
        "server_id": server_id,
        "mcp_name": mcp_name,
        "status": "success" if success else "failed",
        "pattern": classification["pattern"],
        "tool_count": len(tools)
    }


def run() -> int:
    """
    Main entry point for processing MCP tool schema patterns.
    
    Reads MCP tool definitions, classifies patterns, and writes to mcp_signal_enrichments.
    
    Returns:
        Number of servers processed successfully
    """
    logger.info("Starting MCP tool schema pattern analysis")
    
    client = WriteServiceClient(WRITE_SERVICE_HOST, WRITE_SERVICE_PORT)
    health_monitor = HealthMonitor(client)
    
    batch_size = 100
    total_processed = 0
    batch_count = 0
    
    last_health_report = time.time()
    
    while True:
        # Send heartbeat if needed (every <=60s)
        current_time = time.time()
        if current_time - last_health_report >= HEALTH_CHECK_INTERVAL:
            health_monitor.heartbeat(total_processed)
            last_health_report = current_time
        
        # Get tools from fingerprints (primary source)
        fingerprints = _get_mcp_tools_from_fingerprints(client, batch_size)
        
        # Get tools from hashes (alternative source)
        hashes = _get_mcp_tools_from_hashes(client, batch_size)
        
        # Combine and deduplicate by server_id
        all_servers = {}
        for record in fingerprints:
            server_id = record.get("server_id")
            if server_id and server_id not in all_servers:
                all_servers[server_id] = record
        
        for record in hashes:
            server_id = record.get("server_id")
            if server_id and server_id not in all_servers:
                all_servers[server_id] = record
        
        if not all_servers:
            logger.info("No more servers to process")
            break
        
        batch_count += 1
        logger.info(f"Processing batch {batch_count} with {len(all_servers)} servers")
        
        for server_id, server_record in all_servers.items():
            # Check idempotency - skip if already processed
            if _check_existing_enrichment(client, server_id):
                logger.debug(f"Skipping {server_id} - already processed")
                continue
            
            # Process the server
            result = _process_server_tools(client, server_record)
            
            if result["status"] == "success":
                total_processed += 1
                logger.debug(f"Processed {server_id}: {result['pattern']}")
            else:
                logger.warning(f"Failed to process {server_id}: {result.get('reason')}")
        
        logger.info(f"Batch {batch_count} complete. Total processed: {total_processed}")
    
    # Final health report
    health_monitor.heartbeat(total_processed, is_final=True)
    
    logger.info(f"MCP tool schema pattern analysis complete. Processed {total_processed} servers.")
    return total_processed


if __name__ == "__main__":
    # Smoke: import mcp_tool_schema_patterns and verify pattern classification
    from mcp_tool_schema_patterns import classify_tool_schema
    result = classify_tool_schema([{'name': 'cmd', 'description': 'run command'}] * 3)
    assert result['pattern'] in ('progressive_disclosure', 'brute_force_enumeration', 'hybrid')
    print('PASS: tool_schema_patterns classification works')
    
    # Run against a sample of servers
    run()