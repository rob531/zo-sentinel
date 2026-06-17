#!/usr/bin/env python3
"""
signal_analyser_tool_schema_extensions.py

Companion module extending signal_analyser with mcp_tool_schema_patterns detection.
Reads MCP tool definitions, classifies progressive-disclosure vs brute-force enumeration
patterns, and writes results to mcp_signal_enrichments.
"""

import json
import time
import logging
from typing import Optional

import requests

# External library for pattern classification
from mcp_tool_schema_patterns import classify_tool_schema

logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_HOST = "127.0.0.1"
WRITE_SERVICE_PORT = 8772
WRITE_SERVICE_URL = f"http://{WRITE_SERVICE_HOST}:{WRITE_SERVICE_PORT}"
REQUEST_TIMEOUT = 10
HEALTH_CHECK_INTERVAL = 60  # seconds

# Exponential backoff configuration
MAX_RETRIES = 5
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0


class WriteServiceClient:
    """Client for write_service at 127.0.0.1:8772 with retry logic."""

    def __init__(self, base_url: str = WRITE_SERVICE_URL, timeout: int = REQUEST_TIMEOUT):
        self.base_url = base_url
        self.timeout = timeout

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff delay."""
        return min(INITIAL_BACKOFF * (BACKOFF_MULTIPLIER ** attempt), 60.0)

    def _is_retryable_status(self, status_code: int) -> bool:
        """Check if HTTP status indicates a retryable error (5xx)."""
        return 500 <= status_code < 600

    def query(self, sql: str, params: Optional[dict] = None) -> list:
        """Execute a SELECT query against write_service."""
        payload = {"sql": sql}
        if params:
            payload["params"] = params

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self.base_url}/query",
                    json=payload,
                    timeout=self.timeout
                )

                if response.status_code == 200:
                    return response.json().get("data", [])
                elif self._is_retryable_status(response.status_code):
                    backoff = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Retryable error {response.status_code}, attempt {attempt + 1}/{MAX_RETRIES}, "
                        f"backing off {backoff:.1f}s"
                    )
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(backoff)
                        continue
                else:
                    response.raise_for_status()
            except requests.exceptions.Timeout:
                backoff = self._calculate_backoff(attempt)
                logger.warning(
                    f"Request timeout, attempt {attempt + 1}/{MAX_RETRIES}, "
                    f"backing off {backoff:.1f}s"
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                    continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                raise

        raise Exception(f"Max retries ({MAX_RETRIES}) exceeded for query")

    def execute(self, sql: str, params: Optional[dict] = None) -> dict:
        """Execute an INSERT/UPDATE query against write_service."""
        payload = {"sql": sql}
        if params:
            payload["params"] = params

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(
                    f"{self.base_url}/execute",
                    json=payload,
                    timeout=self.timeout
                )

                if response.status_code == 200:
                    return response.json()
                elif self._is_retryable_status(response.status_code):
                    backoff = self._calculate_backoff(attempt)
                    logger.warning(
                        f"Retryable error {response.status_code}, attempt {attempt + 1}/{MAX_RETRIES}, "
                        f"backing off {backoff:.1f}s"
                    )
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(backoff)
                        continue
                else:
                    response.raise_for_status()
            except requests.exceptions.Timeout:
                backoff = self._calculate_backoff(attempt)
                logger.warning(
                    f"Request timeout, attempt {attempt + 1}/{MAX_RETRIES}, "
                    f"backing off {backoff:.1f}s"
                )
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
                    continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                raise

        raise Exception(f"Max retries ({MAX_RETRIES}) exceeded for execute")


class ServiceHealth:
    """Heartbeat service for health monitoring."""

    def __init__(self, client: WriteServiceClient):
        self.client = client
        self.last_heartbeat = 0

    def send_heartbeat(self) -> None:
        """Send heartbeat to service_health."""
        try:
            sql = """
                INSERT INTO service_health (service_name, status, timestamp)
                VALUES ('signal_analyser_tool_schema', 'healthy', NOW())
            """
            self.client.execute(sql)
            self.last_heartbeat = time.time()
            logger.debug("Heartbeat sent to service_health")
        except Exception as e:
            logger.warning(f"Failed to send heartbeat: {e}")

    def check_and_heartbeat(self) -> None:
        """Check if heartbeat is needed and send if sufficient time has passed."""
        elapsed = time.time() - self.last_heartbeat
        if elapsed >= HEALTH_CHECK_INTERVAL:
            self.send_heartbeat()


def _classify_server_tools(tools: list) -> dict:
    """
    Passthrough to mcp_tool_schema_patterns classify_tool_schema.
    
    Args:
        tools: List of tool definitions from MCP server
        
    Returns:
        dict with classification result containing pattern, evidence, tool_count
    """
    try:
        result = classify_tool_schema(tools)
        return result
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {
            "pattern": "unknown",
            "evidence": {"error": str(e)},
            "tool_count": len(tools) if tools else 0
        }


def _get_mcp_tools_from_fingerprints(client: WriteServiceClient, mcp_name: str) -> Optional[list]:
    """Fetch tool definitions from mcp_fingerprints table."""
    sql = """
        SELECT tool_definitions
        FROM mcp_fingerprints
        WHERE mcp_name = ?
        LIMIT 1
    """
    results = client.query(sql, {"mcp_name": mcp_name})
    if results and len(results) > 0:
        tool_defs = results[0].get("tool_definitions")
        if tool_defs:
            if isinstance(tool_defs, str):
                return json.loads(tool_defs)
            return tool_defs
    return None


def _get_mcp_tools_from_hashes(client: WriteServiceClient, mcp_name: str) -> Optional[list]:
    """Fetch tool definitions from mcp_tool_hashes table."""
    sql = """
        SELECT tools
        FROM mcp_tool_hashes
        WHERE mcp_name = ?
        LIMIT 1
    """
    results = client.query(sql, {"mcp_name": mcp_name})
    if results and len(results) > 0:
        tools = results[0].get("tools")
        if tools:
            if isinstance(tools, str):
                return json.loads(tools)
            return tools
    return None


def _get_mcp_server_metadata(client: WriteServiceClient, mcp_name: str) -> Optional[dict]:
    """Fetch server metadata from mcp_server_registry."""
    sql = """
        SELECT mcp_name, registry_source
        FROM mcp_server_registry
        WHERE mcp_name = ?
        LIMIT 1
    """
    results = client.query(sql, {"mcp_name": mcp_name})
    if results and len(results) > 0:
        return results[0]
    return None


def _check_existing_signal(client: WriteServiceClient, mcp_name: str) -> bool:
    """Check if tool_schema_pattern signal already exists for this server."""
    sql = """
        SELECT 1
        FROM mcp_signal_enrichments
        WHERE mcp_name = ?
          AND signal_type = 'tool_schema_pattern'
        LIMIT 1
    """
    results = client.query(sql, {"mcp_name": mcp_name})
    return len(results) > 0


def _write_signal_enrichment(
    client: WriteServiceClient,
    mcp_name: str,
    pattern: str,
    tool_count: int,
    evidence: dict
) -> None:
    """Write classification result to mcp_signal_enrichments."""
    evidence_blob = {
        "pattern": pattern,
        "tool_count": tool_count,
        "evidence": evidence
    }

    sql = """
        INSERT INTO mcp_signal_enrichments 
        (mcp_name, signal_type, evidence_blob, created_at)
        VALUES (?, 'tool_schema_pattern', ?, NOW())
    """
    client.execute(sql, {
        "mcp_name": mcp_name,
        "evidence_blob": json.dumps(evidence_blob)
    })


def _get_all_mcp_servers(client: WriteServiceClient) -> list:
    """Get all MCP servers from the registry."""
    sql = """
        SELECT DISTINCT mcp_name
        FROM mcp_server_registry
    """
    return client.query(sql)


def run(batch_size: int = 100) -> int:
    """
    Main entry point - reads MCPs from DB, processes in batches, writes to mcp_signal_enrichments.
    
    Args:
        batch_size: Number of servers to process per batch
        
    Returns:
        int: Number of servers processed
    """
    client = WriteServiceClient()
    health = ServiceHealth(client)

    logger.info("Starting tool_schema_pattern classification")
    processed_count = 0

    try:
        # Get all MCP servers to process
        servers = _get_all_mcp_servers(client)
        logger.info(f"Found {len(servers)} MCP servers to process")

        for i, server in enumerate(servers):
            mcp_name = server.get("mcp_name")
            if not mcp_name:
                continue

            # Check for existing signal (idempotency)
            if _check_existing_signal(client, mcp_name):
                logger.debug(f"Skipping {mcp_name}: signal already exists")
                continue

            # Try to get tool definitions from fingerprints first
            tools = _get_mcp_tools_from_fingerprints(client, mcp_name)

            # Fallback to mcp_tool_hashes if not found
            if not tools:
                tools = _get_mcp_tools_from_hashes(client, mcp_name)

            if not tools:
                logger.debug(f"No tool definitions found for {mcp_name}")
                continue

            if not isinstance(tools, list):
                logger.warning(f"Invalid tool definitions format for {mcp_name}")
                continue

            # Classify the tools
            classification = _classify_server_tools(tools)

            # Write result to mcp_signal_enrichments
            try:
                _write_signal_enrichment(
                    client=client,
                    mcp_name=mcp_name,
                    pattern=classification.get("pattern", "unknown"),
                    tool_count=len(tools),
                    evidence=classification
                )
                processed_count += 1
                logger.info(f"Processed {mcp_name}: pattern={classification.get('pattern')}")
            except Exception as e:
                logger.error(f"Failed to write enrichment for {mcp_name}: {e}")

            # Check and send heartbeat if needed
            health.check_and_heartbeat()

    except Exception as e:
        logger.error(f"Fatal error in run(): {e}")
        raise
    finally:
        # Ensure final heartbeat is sent
        health.send_heartbeat()

    logger.info(f"Completed: processed {processed_count} servers")
    return processed_count


if __name__ == "__main__":
    # Smoke test: verify mcp_tool_schema_patterns import and classification
    from mcp_tool_schema_patterns import classify_tool_schema

    test_tools = [{"name": "cmd", "description": "run command"}] * 3
    result = classify_tool_schema(test_tools)
    assert result["pattern"] in (
        "progressive_disclosure",
        "brute_force_enumeration",
        "hybrid",
        "unknown"
    ), f"Unexpected pattern: {result['pattern']}"
    print("PASS: tool_schema_patterns classification works")

    # Run against sample servers
    print("Running tool_schema_pattern classification...")
    count = run()
    print(f"Completed: {count} servers processed")