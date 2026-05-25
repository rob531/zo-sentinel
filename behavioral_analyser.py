import requests
import logging
import time
import hashlib
import re
from difflib import SequenceMatcher
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger(__name__)

SERVICE_NAME = "behavioral_analyser"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8773"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300

TRUSTED_MCP_NAMES = [
    "github", "filesystem", "memory", "brave-search", "slack", "discord",
    "git", "aws", "google", "azure", "openai", "anthropic", "postgres",
    "mysql", "redis", "mongodb", "docker", "kubernetes", "notion",
    "linear", "jira", "confluence", "figma", "stripe", "twilio"
]

INJECTION_PATTERNS = {
    "html": re.compile(r'<[^>]+>|\&[a-z]+;', re.IGNORECASE),
    "javascript": re.compile(r'<script[^>]*>|javascript:|on\w+\s*=', re.IGNORECASE),
    "sql": re.compile(r"';\s*(DROP|DELETE|INSERT|UPDATE|SELECT|UNION)", re.IGNORECASE),
    "template_injection": re.compile(r'\{\{.*?\}\}|\$\{.*?\}', re.IGNORECASE),
    "xss_payload": re.compile(r'(alert\s*\(|confirm\s*\(|prompt\s*\()', re.IGNORECASE)
}

PID_FILE = "/tmp/behavioral_analyser.pid"


def check_single_instance() -> bool:
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        try:
            import signal
            os.kill(int(old_pid), 0)
            log.warning(f"Another instance running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            log.info("Stale PID file found, continuing")
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql, "params": params or {}},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get("data", []) if isinstance(result, dict) else result
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed for {table}: {e}")
        return False


def send_heartbeat() -> bool:
    return ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "running",
        "cycle_detections": 0
    }])


def ensure_threat_table() -> None:
    exec_url = f"{EXECUTE_SERVICE_URL}"
    try:
        requests.post(exec_url, json={
            "sql": """
            CREATE TABLE IF NOT EXISTS mcp_threat_associations (
                id          BIGINT PRIMARY KEY,
                server_id   VARCHAR NOT NULL,
                threat_type VARCHAR,
                evidence    TEXT,
                severity    VARCHAR,
                reported_at TIMESTAMPTZ DEFAULT now()
            )
            """,
            "params": {}
        }, timeout=10)
    except Exception as e:
        log.warning(f"Table ensure failed (may exist): {e}")


def edit_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def check_description_injection(description: Optional[str]) -> Optional[Dict[str, Any]]:
    if not description:
        return None
    
    detections = []
    for pattern_name, pattern in INJECTION_PATTERNS.items():
        if pattern.search(description):
            detections.append(f"detected_{pattern_name}")
    
    if detections:
        return {
            "threat_type": "description_injection",
            "evidence": f"Patterns found: {', '.join(detections)}",
            "severity": "high" if "javascript" in detections or "sql" in detections else "medium"
        }
    return None


def check_namespace_squatting(server_name: str) -> Optional[Dict[str, Any]]:
    normalized = server_name.lower().replace("_", "-").replace(" ", "-")
    
    for trusted in TRUSTED_MCP_NAMES:
        dist = edit_distance(normalized, trusted)
        if dist > 0 and dist < 3:
            return {
                "threat_type": "namespace_squatting",
                "evidence": f"Name '{server_name}' is {dist} edits from trusted name '{trusted}'",
                "severity": "high"
            }
    
    for trusted in TRUSTED_MCP_NAMES:
        if trusted in normalized or normalized.startswith(trusted + "-") or normalized.endswith("-" + trusted):
            return {
                "threat_type": "namespace_squatting",
                "evidence": f"Name '{server_name}' contains/mimics trusted name '{trusted}'",
                "severity": "medium"
            }
    
    return None


def detect_rapid_name_changes() -> List[Dict[str, Any]]:
    sql = """
    WITH name_history AS (
        SELECT server_id, name, last_assessed,
               LAG(name) OVER (PARTITION BY server_id ORDER BY last_assessed) as prev_name
        FROM mcp_server_registry
        WHERE name IS NOT NULL
    )
    SELECT server_id, name, prev_name, last_assessed
    FROM name_history
    WHERE prev_name IS NOT NULL AND name != prev_name
    ORDER BY last_assessed DESC
    """
    
    results = ws_query(sql)
    threats = []
    
    for row in results:
        if row.get("prev_name") and row.get("name"):
            threats.append({
                "server_id": row["server_id"],
                "threat_type": "rapid_name_change",
                "evidence": f"Changed from '{row['prev_name']}' to '{row['name']}' on {row.get('last_assessed', 'unknown')}",
                "severity": "medium"
            })
    
    return threats


def detect_permission_escalation(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, snapshot_content, captured_at
    FROM mcp_definition_history
    WHERE server_id = ?
    ORDER BY captured_at ASC
    LIMIT 10
    """
    
    results = ws_query(sql, {"p1": server_id})
    
    if len(results) < 2:
        return []
    
    threats = []
    first_tools = set()
    last_tools = set()
    
    try:
        first_snapshot = json.loads(results[0].get("snapshot_content", "{}"))
        last_snapshot = json.loads(results[-1].get("snapshot_content", "{}"))
        
        if "tools" in first_snapshot:
            first_tools = {t.get("name", "") for t in first_snapshot["tools"] if t.get("name")}
        if "tools" in last_snapshot:
            last_tools = {t.get("name", "") for t in last_snapshot["tools"] if t.get("name")}
        
        new_permissions = last_tools - first_tools
        if len(new_permissions) >= 3:
            threats.append({
                "server_id": server_id,
                "threat_type": "permission_escalation",
                "evidence": f"Added {len(new_permissions)} new tools: {', '.join(list(new_permissions)[:10])}",
                "severity": "high"
            })
    except (json.JSONDecodeError, KeyError) as e:
        log.debug(f"Could not parse snapshot for {server_id}: {e}")
    
    return threats


def detect_phantom_packages() -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, name, registry_source, description
    FROM mcp_server_registry
    WHERE registry_source = 'npm'
    """
    
    results = ws_query(sql)
    threats = []
    
    for row in results:
        desc = row.get("description", "") or ""
        downloads_match = re.search(r'downloads["\']?\s*:\s*([0-9]+)', desc, re.IGNORECASE)
        stars_match = re.search(r'stars["\']?\s*:\s*([0-9]+)', desc, re.IGNORECASE)
        
        if downloads_match and stars_match:
            downloads = int(downloads_match.group(1))
            stars = int(stars_match.group(1))
            
            if downloads == 0 and stars > 5:
                threats.append({
                    "server_id": row["server_id"],
                    "threat_type": "phantom_packages",
                    "evidence": f"0 downloads but {stars} stars - unusual ratio",
                    "severity": "medium"
                })
    
    return threats


def get_all_servers() -> List[str]:
    sql = "SELECT DISTINCT server_id FROM mcp_server_registry"
    results = ws_query(sql)
    return [r.get("server_id") for r in results if r.get("server_id")]


def run_analysis_cycle() -> int:
    log.info("Starting behavioral analysis cycle")
    ensure_threat_table()
    
    all_threats = []
    all_threats.extend(detect_rapid_name_changes())
    log.info(f"Found {len([t for t in all_threats if t['threat_type'] == 'rapid_name_change'])} rapid name changes")
    
    all_threats.extend(detect_phantom_packages())
    log.info(f"Found {len([t for t in all_threats if t['threat_type'] == 'phantom_packages'])} phantom packages")
    
    servers = get_all_servers()
    log.info(f"Checking permission escalation for {len(servers)} servers")
    
    for server_id in servers:
        threats = detect_permission_escalation(server_id)
        all_threats.extend(threats)
    
    all_servers = ws_query("""
        SELECT server_id, name, description FROM mcp_server_registry
    """)
    
    squatting_count = 0
    injection_count = 0
    
    for row in all_servers:
        server_id = row.get("server_id", "")
        name = row.get("name", "")
        desc = row.get("description", "")
        
        squatting = check_namespace_squatting(name)
        if squatting:
            squatting["server_id"] = server_id
            all_threats.append(squatting)
            squatting_count += 1
        
        injection = check_description_injection(desc)
        if injection:
            injection["server_id"] = server_id
            all_threats.append(injection)
            injection_count += 1
    
    log.info(f"Found {squatting_count} namespace squatting, {injection_count} description injection")
    
    if all_threats:
        success = ws_write("mcp_threat_associations", all_threats)
        log.info(f"Wrote {len(all_threats)} threat associations to database")
    else:
        log.info("No behavioral threats detected this cycle")
    
    return len(all_threats)


def run():
    if not check_single_instance():
        log.info("Instance check failed, exiting")
        return
    
    log.info(f"{SERVICE_NAME} starting")
    
    send_heartbeat()
    
    log.info(f"Running analysis every {HEARTBEAT_INTERVAL}s with full cycle every 43200s")
    
    cycle_count = 0
    while True:
        try:
            cycle_count += 1
            
            if cycle_count % 144 == 0:
                detection_count = run_analysis_cycle()
                log.info(f"Completed analysis cycle #{cycle_count}, detected {detection_count} threats")
            else:
                send_heartbeat()
            
            time.sleep(HEARTBEAT_INTERVAL)
            
        except KeyboardInterrupt:
            log.info("Shutdown requested")
            break
        except Exception as e:
            log.error(f"Error in main loop: {e}")
            time.sleep(60)


if __name__ == "__main__":
    import json
    run()