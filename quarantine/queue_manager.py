#!/usr/bin/env python3
"""
queue_manager.py -- ZO-SENTINEL assessment queue manager.
Manages prioritization of which MCPs to assess next.
No daemon, utility module used by signal_analyser.
"""
import requests
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8773"
EXECUTE_URL = "http://127.0.0.1:8772/execute"

ASSESSMENT_QUEUE_FILE = "ASSESSMENT_QUEUE.md"
PRIORITY_THRESHOLD_DAYS = 7
APPROVAL_WORKFLOW_TABLE = "approval_workflow"
MESH_EVENTS_TABLE = "mesh_events"
NPM_SCOPE_KEYWORDS = ["@modelcontextprotocol", "@mcp", "mcp-server", "model-context-protocol"]


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a query via inference_router (DuckDB)."""
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        log.warning(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write rows to table via write_service."""
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed to {table}: {e}")
        return False


def get_approval_submitted_servers() -> set:
    """Get server_ids explicitly submitted via approval_workflow (highest priority)."""
    query = """
        SELECT DISTINCT server_id 
        FROM approval_workflow 
        WHERE server_id IS NOT NULL 
        AND (status IS NULL OR status != 'completed')
    """
    results = ws_query(query)
    return {row.get("server_id") for row in results if row.get("server_id")}


def get_mesh_submitted_servers() -> set:
    """Get server_ids submitted via mesh_events (approval workflow submissions)."""
    query = """
        SELECT DISTINCT server_id 
        FROM mesh_events 
        WHERE event_type = 'approval_submitted' 
        AND server_id IS NOT NULL
    """
    results = ws_query(query)
    return {row.get("server_id") for row in results if row.get("server_id")}


def get_npm_official_servers() -> set:
    """Get servers discovered from npm official scope."""
    query = """
        SELECT server_id 
        FROM mcp_server_registry 
        WHERE (
            LOWER(name) LIKE '%@modelcontextprotocol%' 
            OR LOWER(name) LIKE '%@mcp%'
            OR LOWER(name) LIKE '%mcp-server%'
            OR LOWER(description) LIKE '%model context protocol%'
            OR registry_source = 'npm_official'
        )
        AND server_id IS NOT NULL
    """
    results = ws_query(query)
    return {row.get("server_id") for row in results if row.get("server_id")}


def get_high_scan_count_servers(limit: int = 50) -> List[Dict[str, Any]]:
    """Get servers with high scan_count but no verdict."""
    query = f"""
        SELECT server_id, scan_count, last_assessed, name
        FROM mcp_server_registry 
        WHERE verdict IS NULL 
        OR verdict = ''
        OR verdict = 'unknown'
        AND scan_count > 0
        ORDER BY scan_count DESC
        LIMIT {limit}
    """
    return ws_query(query)


def get_stale_servers(days_threshold: int = 7) -> List[Dict[str, Any]]:
    """Get servers last assessed > N days ago."""
    threshold_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()
    query = f"""
        SELECT server_id, scan_count, last_assessed, name
        FROM mcp_server_registry 
        WHERE last_assessed IS NOT NULL 
        AND last_assessed < '{threshold_date}'
        AND (verdict IS NULL OR verdict = '' OR verdict = 'unknown')
        ORDER BY last_assessed ASC
        LIMIT 100
    """
    return ws_query(query)


def get_all_unscored_servers() -> List[Dict[str, Any]]:
    """Get all servers without a verdict."""
    query = """
        SELECT server_id, scan_count, last_assessed, name, url, description
        FROM mcp_server_registry 
        WHERE verdict IS NULL 
        OR verdict = ''
        OR verdict = 'unknown'
        ORDER BY last_seen DESC
    """
    return ws_query(query)


def build_priority_queue() -> List[Dict[str, Any]]:
    """
    Build ordered priority queue of servers to assess.
    Priority order:
    1. Servers explicitly submitted via approval_workflow (highest)
    2. Servers discovered from npm official scope
    3. Servers with high scan_count but no verdict
    4. Servers last assessed >7d ago
    """
    priority_queue = []
    seen_ids = set()
    
    # Priority 1: Approval workflow submissions
    approval_servers = get_approval_submitted_servers()
    mesh_servers = get_mesh_submitted_servers()
    all_submitted = approval_servers | mesh_servers
    
    for server_id in all_submitted:
        if server_id and server_id not in seen_ids:
            query = f"""
                SELECT server_id, name, scan_count, last_assessed, url
                FROM mcp_server_registry 
                WHERE server_id = '{server_id}'
            """
            results = ws_query(query)
            if results:
                priority_queue.append({
                    "server_id": server_id,
                    "priority": 1,
                    "reason": "approval_submitted",
                    "data": results[0]
                })
                seen_ids.add(server_id)
    
    # Priority 2: NPM official scope
    npm_servers = get_npm_official_servers()
    for server_id in npm_servers:
        if server_id and server_id not in seen_ids:
            query = f"""
                SELECT server_id, name, scan_count, last_assessed, url
                FROM mcp_server_registry 
                WHERE server_id = '{server_id}'
            """
            results = ws_query(query)
            if results:
                priority_queue.append({
                    "server_id": server_id,
                    "priority": 2,
                    "reason": "npm_official_scope",
                    "data": results[0]
                })
                seen_ids.add(server_id)
    
    # Priority 3: High scan count servers
    high_scan = get_high_scan_count_servers(limit=50)
    for row in high_scan:
        server_id = row.get("server_id")
        if server_id and server_id not in seen_ids:
            priority_queue.append({
                "server_id": server_id,
                "priority": 3,
                "reason": "high_scan_count",
                "data": row
            })
            seen_ids.add(server_id)
    
    # Priority 4: Stale servers (>7 days)
    stale = get_stale_servers(days_threshold=PRIORITY_THRESHOLD_DAYS)
    for row in stale:
        server_id = row.get("server_id")
        if server_id and server_id not in seen_ids:
            priority_queue.append({
                "server_id": server_id,
                "priority": 4,
                "reason": "stale_assessment",
                "data": row
            })
            seen_ids.add(server_id)
    
    return priority_queue


def get_next_batch(n: int = 10) -> List[str]:
    """
    Get next batch of server_ids to assess.
    Returns ordered list of server_ids in priority order.
    """
    queue = build_priority_queue()
    batch = []
    
    for item in queue[:n]:
        server_id = item.get("server_id")
        if server_id:
            batch.append(server_id)
    
    return batch


def write_assessment_queue_file(queue: List[Dict[str, Any]]) -> bool:
    """Write priority queue to ASSESSMENT_QUEUE.md file."""
    try:
        lines = [
            "# ZO-SENTINEL Assessment Queue",
            f"Generated: {datetime.now().isoformat()}",
            "",
            "## Priority Legend",
            "- Priority 1: Approval workflow submissions (highest)",
            "- Priority 2: NPM official scope",
            "- Priority 3: High scan count",
            "- Priority 4: Stale assessment (>7 days)",
            "",
            "## Queue",
            "",
        ]
        
        lines.append("| Priority | Server ID | Reason | Name | Scan Count | Last Assessed |")
        lines.append("|----------|-----------|--------|------|------------|--------------|")
        
        for item in queue:
            data = item.get("data", {})
            last_assessed = data.get("last_assessed", "Never")
            if last_assessed and last_assessed != "Never":
                try:
                    dt = datetime.fromisoformat(last_assessed.replace("Z", "+00:00"))
                    last_assessed = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            
            lines.append(
                f"| {item['priority']} | "
                f"{item['server_id']} | "
                f"{item['reason']} | "
                f"{data.get('name', 'N/A')} | "
                f"{data.get('scan_count', 0)} | "
                f"{last_assessed} |"
            )
        
        lines.append("")
        lines.append(f"**Total in queue: {len(queue)}**")
        
        with open(ASSESSMENT_QUEUE_FILE, "w") as f:
            f.write("\n".join(lines))
        
        log.info(f"Wrote assessment queue with {len(queue)} entries to {ASSESSMENT_QUEUE_FILE}")
        return True
        
    except Exception as e:
        log.error(f"Failed to write queue file: {e}")
        return False


def get_queue_stats() -> Dict[str, Any]:
    """Get statistics about the current queue."""
    queue = build_priority_queue()
    
    stats = {
        "total": len(queue),
        "by_priority": {},
        "timestamp": datetime.now().isoformat()
    }
    
    for item in queue:
        priority = item["priority"]
        stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1
    
    return stats


def refresh_queue() -> List[Dict[str, Any]]:
    """Build queue and write to file. Returns the queue."""
    queue = build_priority_queue()
    write_assessment_queue_file(queue)
    return queue


def get_queue_for_signal_analyser(batch_size: int = 10) -> List[str]:
    """
    Get next batch of server_ids for signal_analyser.
    Convenience function that builds queue, writes file, and returns batch.
    """
    queue = refresh_queue()
    return [item["server_id"] for item in queue[:batch_size]]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    log.info("Building assessment queue...")
    queue = refresh_queue()
    
    stats = get_queue_stats()
    log.info(f"Queue stats: {stats}")
    
    batch = get_next_batch(n=10)
    log.info(f"Next batch ({len(batch)} servers): {batch[:5]}...")
    
    log.info(f"Full queue written to {ASSESSMENT_QUEUE_FILE}")