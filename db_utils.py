#!/usr/bin/env python3
"""
db_utils.py -- ZO-SENTINEL shared database utility module.
Provides retry-aware wrappers for write_service operations.
"""
import requests
import time
import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

WRITE_SERVICE = "http://127.0.0.1:8772"
EXECUTE_URL = f"{WRITE_SERVICE}/execute"
QUERY_URL = f"{WRITE_SERVICE}/query"
WRITE_URL = f"{WRITE_SERVICE}/write"

MAX_RETRIES = 3
RETRY_BACKOFF = 2


def _retry_request(func, *args, **kwargs):
    """Execute a request function with retry logic and exponential backoff.
    
    Args:
        func: Request function to execute
        *args: Positional arguments for the function
        **kwargs: Keyword arguments for the function
    
    Returns:
        Response JSON from the request
    
    Raises:
        Exception: If all retries are exhausted
    """
    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except (ConnectionError, requests.exceptions.ConnectionError) as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_BACKOFF * (attempt + 1)
                log.warning(
                    f"Connection attempt {attempt + 1}/{MAX_RETRIES} failed: {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                log.error(f"All {MAX_RETRIES} connection attempts exhausted")
        except requests.exceptions.RequestException as e:
            last_exception = e
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_BACKOFF * (attempt + 1)
                log.warning(
                    f"Request attempt {attempt + 1}/{MAX_RETRIES} failed: {e}. "
                    f"Retrying in {wait_time}s..."
                )
                time.sleep(wait_time)
            else:
                log.error(f"All {MAX_RETRIES} request attempts exhausted: {e}")
    
    if last_exception:
        raise last_exception


def ws_query(sql: str, params: Optional[list] = None) -> list:
    """Execute SQL query against DuckDB via write_service with retry logic.
    
    Args:
        sql: SQL query string
        params: Optional list of parameters for parameterized queries
    
    Returns:
        list of result rows
    
    Raises:
        Exception: If query fails after all retries
    """
    payload: dict[str, Any] = {"sql": sql}
    if params is not None:
        payload["params"] = params
    
    def _do_query():
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    
    return _retry_request(_do_query)


def ws_write(table: str, row: dict, wait: bool = True) -> bool:
    """Write a row to a table via write_service with retry logic.
    
    Args:
        table: Target table name
        row: Dictionary of column-value pairs to write
        wait: Whether to wait for synchronous confirmation
    
    Returns:
        True if write succeeded
    
    Raises:
        Exception: If write fails after all retries
    """
    payload: dict[str, Any] = {"table": table, "rows": row, "wait": wait}
    
    def _do_write():
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    
    return _retry_request(_do_write)


def ws_execute(sql: str) -> bool:
    """Execute SQL statement via write_service with retry logic.
    
    Args:
        sql: SQL statement to execute
    
    Returns:
        True if execution succeeded
    
    Raises:
        Exception: If execution fails after all retries
    """
    payload = {"sql": sql}
    
    def _do_execute():
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    
    return _retry_request(_do_execute)


def ws_heartbeat(service_name: str) -> bool:
    """Record service heartbeat in service_health table.
    
    Args:
        service_name: Name of the service sending heartbeat
    
    Returns:
        True if heartbeat recorded successfully
    """
    try:
        return ws_write("service_health", {
            "service": service_name,
            "last_heartbeat": datetime.now(timezone.utc).isoformat()
        }, wait=True)
    except Exception as e:
        log.error(f"Heartbeat failed for {service_name}: {e}")
        return False


def get_unscored_servers(limit: int = 20) -> list:
    """Retrieve servers that have not yet been scored by the signal analyser.
    
    Args:
        limit: Maximum number of servers to return
    
    Returns:
        List of server records missing signal scores
    """
    sql = """
    SELECT ms.id, ms.server_id, ms.name, ms.definition_hash,
           ms.risk_tier, ms.director_maturity_level, ms.status,
           ms.last_seen, ms.tags
    FROM mcp_servers ms
    LEFT JOIN mcp_signal_scores mss ON ms.server_id = mss.server_id
    WHERE mss.id IS NULL
    ORDER BY ms.last_seen DESC
    LIMIT ?
    """
    try:
        return ws_query(sql, [limit])
    except Exception as e:
        log.error(f"Failed to fetch unscored servers: {e}")
        return []


def get_server_by_id(server_id: str) -> Optional[dict]:
    """Retrieve a specific server by its server_id.
    
    Args:
        server_id: Unique server identifier
    
    Returns:
        Server record dictionary or None if not found
    """
    sql = """
    SELECT id, server_id, name, definition, definition_hash,
           risk_tier, director_maturity_level, last_seen, status, tags
    FROM mcp_servers
    WHERE server_id = ?
    """
    try:
        result = ws_query(sql, [server_id])
        return result[0] if result else None
    except Exception as e:
        log.error(f"Failed to fetch server {server_id}: {e}")
        return None


def get_server_signals(server_id: str) -> list:
    """Retrieve all signal scores for a given server.
    
    Args:
        server_id: Unique server identifier
    
    Returns:
        List of signal score records for the server
    """
    sql = """
    SELECT id, server_id, signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY scored_at DESC
    """
    try:
        return ws_query(sql, [server_id])
    except Exception as e:
        log.error(f"Failed to fetch signals for server {server_id}: {e}")
        return []


def get_server_history(server_id: str, limit: int = 10) -> list:
    """Retrieve definition history snapshots for a server.
    
    Args:
        server_id: Unique server identifier
        limit: Maximum number of snapshots to return
    
    Returns:
        List of historical snapshots ordered by capture time descending
    """
    sql = """
    SELECT id, server_id, snapshot_hash, snapshot_content, captured_at
    FROM mcp_definition_history
    WHERE server_id = ?
    ORDER BY captured_at DESC
    LIMIT ?
    """
    try:
        return ws_query(sql, [server_id, limit])
    except Exception as e:
        log.error(f"Failed to fetch history for server {server_id}: {e}")
        return []


def get_threat_associations(server_id: str) -> list:
    """Retrieve threat associations for a given server.
    
    Args:
        server_id: Unique server identifier
    
    Returns:
        List of threat association records
    """
    sql = """
    SELECT id, server_id, threat_type, evidence, severity, reported_at
    FROM mcp_threat_associations
    WHERE server_id = ?
    ORDER BY reported_at DESC
    """
    try:
        return ws_query(sql, [server_id])
    except Exception as e:
        log.error(f"Failed to fetch threats for server {server_id}: {e}")
        return []


def get_all_servers(limit: int = 100, offset: int = 0) -> list:
    """Retrieve all registered servers with pagination.
    
    Args:
        limit: Maximum number of records to return
        offset: Number of records to skip
    
    Returns:
        List of server records
    """
    sql = """
    SELECT id, server_id, name, definition_hash, risk_tier,
           director_maturity_level, status, last_seen, tags
    FROM mcp_servers
    ORDER BY last_seen DESC
    LIMIT ? OFFSET ?
    """
    try:
        return ws_query(sql, [limit, offset])
    except Exception as e:
        log.error(f"Failed to fetch servers: {e}")
        return []


def get_services_health() -> list:
    """Retrieve health status of all registered services.
    
    Returns:
        List of service health records
    """
    sql = """
    SELECT service, last_heartbeat, status
    FROM service_health
    ORDER BY last_heartbeat DESC
    """
    try:
        return ws_query(sql)
    except Exception as e:
        log.error(f"Failed to fetch service health: {e}")
        return []


def get_articles_by_topic(topic: str, limit: int = 20) -> list:
    """Retrieve world articles filtered by topic.
    
    Args:
        topic: Topic to filter articles by
        limit: Maximum number of articles to return
    
    Returns:
        List of article records matching the topic
    """
    sql = """
    SELECT id, title, url, topics, content, published_at, ingested_at
    FROM world_articles
    WHERE topics LIKE ?
    ORDER BY published_at DESC
    LIMIT ?
    """
    try:
        return ws_query(sql, [f"%{topic}%", limit])
    except Exception as e:
        log.error(f"Failed to fetch articles for topic {topic}: {e}")
        return []