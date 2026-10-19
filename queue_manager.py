import requests
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

ASSESSMENT_QUEUE_FILE = "ASSESSMENT_QUEUE.md"
PRIORITY_THRESHOLD_DAYS = 7

MCP_SERVER_REGISTRY = "mcp_server_registry"
MCP_SIGNAL_SCORES = "mcp_signal_scores"
MCP_RISK_REGISTER = "mcp_risk_register"
MESH_EVENTS_TABLE = "mesh_events"
NPM_SCOPE_KEYWORDS = ["@modelcontextprotocol", "@mcp", "mcp-server", "model-context-protocol"]

RISK_TIER_ORDER = {
    "CRITICAL": 1,
    "HIGH": 2,
    "MEDIUM": 3,
    "LOW": 4,
    "MINIMAL": 5,
    "UNKNOWN": 99,
}


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a SELECT query via write_service."""
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.warning(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to table via write_service."""
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json=payload,
            timeout=15
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed to {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute failed: {sql[:100]}... Error: {e}")
        return False


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_mesh_submitted_servers() -> set:
    """Get server_ids submitted via mesh events (mesh_memory SQLite)."""
    import sqlite3
    servers = set()
    try:
        conn = sqlite3.connect("/home/workspace/Datasets/zo-mesh/mesh_memory.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT server_id 
            FROM mesh_events 
            WHERE server_id IS NOT NULL
            AND event_type IN ('mcp_submitted', 'mcp_candidate_added')
        """)
        for row in cursor.fetchall():
            servers.add(row[0])
        conn.close()
    except Exception as e:
        log.warning(f"mesh_memory query failed: {e}")
    return servers


def get_npm_official_servers() -> set:
    """Get server_ids from NPM official registry sources."""
    query = """
        SELECT server_id 
        FROM mcp_server_registry 
        WHERE registry_source IN ('npm', 'github', 'smithery')
        AND verdict IS NULL
        AND trust_score < 50
    """
    results = ws_query(query)
    return {row.get("server_id") for row in results if row.get("server_id")}


def get_unscored_servers(limit: int = 100) -> List[Dict[str, Any]]:
    """Get servers missing signal scores, ordered by risk priority."""
    query = f"""
        SELECT 
            r.server_id,
            r.name,
            r.url,
            r.registry_source,
            r.verdict,
            r.trust_score,
            r.scan_count,
            COALESCE(rr.risk_tier, 'UNKNOWN') as risk_tier,
            COALESCE(rr.threat_count, 0) as threat_count
        FROM mcp_server_registry r
        LEFT JOIN mcp_risk_register rr ON r.server_id = rr.server_id
        WHERE r.verdict IS NULL
        OR r.verdict = 'UNKNOWN'
        ORDER BY
            CASE COALESCE(rr.risk_tier, 'UNKNOWN')
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                WHEN 'MINIMAL' THEN 5
                ELSE 99
            END,
            COALESCE(rr.threat_count, 0) DESC,
            r.scan_count ASC,
            r.trust_score ASC
        LIMIT {limit}
    """
    return ws_query(query)


def get_stale_servers(days: int = 7) -> List[Dict[str, Any]]:
    """Get servers with stale assessments needing refresh."""
    cutoff = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    query = f"""
        SELECT 
            server_id,
            name,
            verdict,
            trust_score,
            last_assessed
        FROM mcp_server_registry
        WHERE last_assessed IS NOT NULL
        AND last_assessed < '{cutoff}'
        AND verdict IS NOT NULL
        AND verdict != 'UNKNOWN'
        ORDER BY last_assessed ASC
        LIMIT 50
    """
    return ws_query(query)


def get_high_risk_unassessed() -> List[Dict[str, Any]]:
    """Get high-risk servers that have never been assessed."""
    query = """
        SELECT 
            r.server_id,
            r.name,
            r.url,
            r.registry_source,
            COALESCE(rr.risk_tier, 'UNKNOWN') as risk_tier,
            COALESCE(rr.threat_count, 0) as threat_count
        FROM mcp_server_registry r
        INNER JOIN mcp_risk_register rr ON r.server_id = rr.server_id
        WHERE rr.risk_tier IN ('CRITICAL', 'HIGH')
        AND (r.verdict IS NULL OR r.verdict = 'UNKNOWN')
        ORDER BY 
            CASE rr.risk_tier WHEN 'CRITICAL' THEN 1 ELSE 2 END,
            rr.threat_count DESC
        LIMIT 20
    """
    return ws_query(query)


def compute_queue_priority(server: Dict[str, Any]) -> int:
    """Compute numeric priority for a server (lower = higher priority)."""
    tier = server.get("risk_tier", "UNKNOWN")
    threat_count = server.get("threat_count", 0)
    trust_score = server.get("trust_score", 50)
    scan_count = server.get("scan_count", 0)
    
    tier_rank = RISK_TIER_ORDER.get(tier, 99)
    
    return (
        tier_rank * 100000 +
        min(threat_count, 100) * 1000 +
        (100 - min(trust_score, 100)) * 10 +
        min(scan_count, 100)
    )


def build_assessment_queue(limit: int = 100) -> List[Dict[str, Any]]:
    """Build ordered assessment queue combining all priority sources."""
    queue = []
    seen_ids = set()
    
    high_risk = get_high_risk_unassessed()
    for server in high_risk:
        sid = server.get("server_id")
        if sid and sid not in seen_ids:
            server["priority"] = compute_queue_priority(server)
            server["queue_source"] = "high_risk"
            queue.append(server)
            seen_ids.add(sid)
    
    unscored = get_unscored_servers(limit=limit)
    for server in unscored:
        sid = server.get("server_id")
        if sid and sid not in seen_ids:
            server["priority"] = compute_queue_priority(server)
            server["queue_source"] = "unscored"
            queue.append(server)
            seen_ids.add(sid)
    
    queue.sort(key=lambda x: x["priority"])
    return queue[:limit]


def get_queue_stats() -> Dict[str, Any]:
    """Return statistics about the current assessment queue."""
    total = get_unscored_servers(limit=10000)
    high_risk = get_high_risk_unassessed()
    mesh_submitted = get_mesh_submitted_servers()
    npm_official = get_npm_official_servers()
    
    unscored_ids = {s["server_id"] for s in total if s.get("server_id")}
    
    return {
        "total_unassessed": len(total),
        "high_risk_pending": len(high_risk),
        "mesh_submitted_count": len(mesh_submitted),
        "npm_official_count": len(npm_official),
        "mesh_in_unassessed": len(mesh_submitted & unscored_ids),
        "npm_in_unassessed": len(npm_official & unscored_ids),
        "generated_at": utc_now_iso(),
    }


def mark_assessment_complete(server_id: str, new_verdict: str, new_trust_score: float) -> bool:
    """Record that a server has been assessed."""
    row = {
        "server_id": server_id,
        "verdict": new_verdict,
        "trust_score": new_trust_score,
        "last_assessed": utc_now_iso(),
    }
    return ws_write(MCP_SERVER_REGISTRY, [row])


def get_next_assessment_target() -> Optional[Dict[str, Any]]:
    """Get the highest-priority server for next assessment."""
    queue = build_assessment_queue(limit=1)
    return queue[0] if queue else None


def log_queue_event(event_type: str, server_id: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    """Log an event to mesh_events for queue tracking."""
    event = {
        "event_type": event_type,
        "server_id": server_id,
        "timestamp": utc_now_iso(),
        "details_json": details or {},
    }
    ws_write(MESH_EVENTS_TABLE, [event])


def ensure_queue_table() -> None:
    """Ensure mesh_events table exists in mesh_memory."""
    import sqlite3
    try:
        conn = sqlite3.connect("/home/workspace/Datasets/zo-mesh/mesh_memory.db")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mesh_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                server_id TEXT,
                timestamp TEXT NOT NULL,
                details_json TEXT,
                processed INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"Failed to create mesh_events table: {e}")


if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    )
    
    ensure_queue_table()
    stats = get_queue_stats()
    print(f"Queue Stats: {stats}")
    
    queue = build_assessment_queue(limit=20)
    print(f"\nTop 20 Assessment Queue:")
    for i, server in enumerate(queue, 1):
        print(f"  {i}. [{server.get('queue_source')}] {server.get('name')} ({server.get('server_id')}) - tier={server.get('risk_tier')}, threats={server.get('threat_count')}")
    
    sys.exit(0)