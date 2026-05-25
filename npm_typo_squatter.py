#!/usr/bin/env python3
"""
npm_typo_squatter.py -- ZO-SENTINEL NPM typo-squatting hunter daemon.
Detects potential typo-squatting attacks against legitimate MCP servers
by comparing npm registry packages against known server names.
"""
import requests
import time
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger(__name__)

# Service endpoints
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"

# Constants
SERVICE_NAME = "npm_typo_squatter"
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 86400

# Thresholds
LEVENSHTEIN_THRESHOLD = 2
MIN_DOWNLOADS_SUSPICIOUS = 100
PUBLICATION_DAYS_SUSPICIOUS = 30
MIN_NAME_LENGTH = 6

# npm API endpoints
NPM_REGISTRY_SEARCH = "https://registry.npmjs.org/-/v1/search"
NPM_DOWNLOADS_API = "https://api.npmjs.org/downloads/point/last-month"

# Homoglyph replacement rules for detection
HOMOGLYPH_REPLACEMENTS = {
    "0": "o",
    "1": "l",
    "rn": "m",
    "vv": "w",
    "ii": "l",
    "aa": "a",
    "ee": "e",
    "oo": "o",
}


def levenshtein(a: str, b: str) -> int:
    """Calculate Levenshtein distance using dynamic programming O(mn).
    
    Args:
        a: First string
        b: Second string
    
    Returns:
        Integer distance between the two strings
    """
    if not a:
        return len(b)
    if not b:
        return len(a)
    
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1]
            else:
                dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])
    
    return dp[m][n]


def apply_homoglyph_normalization(name: str) -> str:
    """Apply homoglyph normalization to detect character substitution attacks.
    
    Args:
        name: Package name to normalize
    
    Returns:
        Normalized name with homoglyphs replaced
    """
    normalized = name.lower()
    for old, new in HOMOGLYPH_REPLACEMENTS.items():
        normalized = normalized.replace(old, new)
    return normalized


def check_homoglyph_similarity(name1: str, name2: str) -> bool:
    """Check if names are similar after homoglyph normalization.
    
    Args:
        name1: First package name
        name2: Second package name
    
    Returns:
        True if normalized names are similar
    """
    norm1 = apply_homoglyph_normalization(name1)
    norm2 = apply_homoglyph_normalization(name2)
    
    if norm1 == norm2:
        return True
    
    dist = levenshtein(norm1, norm2)
    max_len = max(len(norm1), len(norm2))
    threshold = max(1, max_len // 4)
    
    return dist <= threshold


def is_scope_variant(name1: str, name2: str) -> bool:
    """Check if names are scope variants of each other.
    E.g., @modelcontextprotocol/server-filesystem vs modelcontextprotocol-server-filesystem
    
    Args:
        name1: First package name
        name2: Second package name
    
    Returns:
        True if names are scope variants
    """
    def extract_name(name: str) -> str:
        if name.startswith("@"):
            parts = name.split("/")
            if len(parts) >= 2:
                return parts[1]
            return name.lstrip("@")
        return name.replace("mcp-", "").replace("-mcp", "").replace("_", "-")
    
    base1 = extract_name(name1)
    base2 = extract_name(name2)
    
    if not base1 or not base2:
        return False
    
    if len(base1) < MIN_NAME_LENGTH or len(base2) < MIN_NAME_LENGTH:
        return False
    
    norm1 = base1.lower().replace(" ", "-").replace("_", "-").replace("-", "")
    norm2 = base2.lower().replace(" ", "-").replace("_", "-").replace("-", "")
    
    if norm1 == norm2:
        return True
    
    if len(norm1) > 3 and len(norm2) > 3:
        if norm1.startswith(norm2[:4]) or norm2.startswith(norm1[:4]):
            dist = levenshtein(norm1, norm2)
            return dist <= 2
    
    return False


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a query via write_service query endpoint.
    
    Args:
        sql: SQL query string
        params: Optional query parameters
    
    Returns:
        List of result rows as dictionaries
    """
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", data.get("data", []))
    except Exception as e:
        log.error(f"Query failed: {sql[:100]}... Error: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service.
    
    Args:
        table: Target table name
        rows: List of row dictionaries to insert
    
    Returns:
        True if successful, False otherwise
    """
    if not rows:
        return True
    
    payload = {"table": table, "rows": rows}
    
    try:
        resp = requests.post(WRITE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed to {table}: {e}")
        return False


def ensure_tables() -> None:
    """Create required tables if they don't exist."""
    create_npm_alerts = """
    CREATE TABLE IF NOT EXISTS npm_typosquat_alerts (
        id BIGINT PRIMARY KEY,
        suspect_name VARCHAR NOT NULL,
        target_name VARCHAR NOT NULL,
        levenshtein_dist INTEGER,
        npm_downloads BIGINT,
        published_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        alert_sent BOOLEAN DEFAULT FALSE
    )
    """
    
    create_threat_assoc = """
    CREATE TABLE IF NOT EXISTS mcp_threat_associations (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence TEXT,
        severity VARCHAR,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    
    for sql in [create_npm_alerts, create_threat_assoc]:
        try:
            requests.post(EXECUTE_URL, json={"sql": sql}, timeout=10)
        except Exception as e:
            log.warning(f"Table creation warning: {e}")


def search_npm_registry(query: str, size: int = 20) -> List[Dict[str, Any]]:
    """Search npm registry for packages matching query.
    
    Args:
        query: Search query string
        size: Number of results to return
    
    Returns:
        List of npm package search results
    """
    try:
        resp = requests.get(
            NPM_REGISTRY_SEARCH,
            params={"text": query, "size": size},
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        
        results = []
        for item in data.get("objects", []):
            pkg = item.get("package", {})
            results.append({
                "name": pkg.get("name", ""),
                "version": pkg.get("version", ""),
                "date": item.get("date"),
                "score": item.get("score", {}),
            })
        return results
    except Exception as e:
        log.error(f"npm registry search failed for '{query}': {e}")
        return []


def get_npm_downloads(package_name: str) -> Optional[int]:
    """Get npm download count for a package.
    
    Args:
        package_name: npm package name
    
    Returns:
        Download count or None if unavailable
    """
    try:
        resp = requests.get(
            f"{NPM_DOWNLOADS_API}/{package_name}",
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("downloads", 0)
    except Exception as e:
        log.warning(f"Failed to get downloads for {package_name}: {e}")
    return None


def is_recent_publication(published_at: Optional[str], max_days: int = 30) -> bool:
    """Check if publication date is recent.
    
    Args:
        published_at: ISO date string of publication
        max_days: Maximum age in days
    
    Returns:
        True if publication is within max_days
    """
    if not published_at:
        return False
    
    try:
        pub_date = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age = (now - pub_date.replace(tzinfo=timezone.utc)).days
        return 0 <= age <= max_days
    except Exception:
        return False


def check_recent_alert(suspect_name: str, target_name: str) -> bool:
    """Check if we already alerted for this suspect/target combo recently.
    
    Args:
        suspect_name: Suspect package name
        target_name: Target server name
    
    Returns:
        True if recent alert exists
    """
    sql = """
    SELECT COUNT(*) as cnt FROM npm_typosquat_alerts
    WHERE suspect_name = ? AND target_name = ?
    AND created_at > now() - INTERVAL '7 days'
    """
    results = ws_query(sql, {"p1": suspect_name, "p2": target_name})
    if results and results[0].get("cnt", 0) > 0:
        return True
    return False


def create_typosquat_alert(
    suspect_name: str,
    target_name: str,
    lev_dist: int,
    downloads: Optional[int],
    published_at: Optional[str]
) -> bool:
    """Create a typo-squatting alert record.
    
    Args:
        suspect_name: Suspect package name
        target_name: Target server name
        lev_dist: Levenshtein distance
        downloads: npm download count
        published_at: Publication date
    
    Returns:
        True if alert created successfully
    """
    if check_recent_alert(suspect_name, target_name):
        log.debug(f"Skipping duplicate alert for {suspect_name} -> {target_name}")
        return False
    
    alert_row = {
        "suspect_name": suspect_name,
        "target_name": target_name,
        "levenshtein_dist": lev_dist,
        "npm_downloads": downloads,
        "published_at": published_at,
    }
    
    success = ws_write("npm_typosquat_alerts", [alert_row])
    
    if success:
        log.warning(
            f"TYPOSQUAT ALERT: {suspect_name} (dist={lev_dist}, "
            f"downloads={downloads}) targets {target_name}"
        )
        
        server_id_sql = """
        SELECT server_id FROM mcp_server_registry
        WHERE name = ? LIMIT 1
        """
        server_results = ws_query(server_id_sql, {"p1": target_name})
        
        if server_results:
            server_id = server_results[0].get("server_id")
            threat_row = {
                "server_id": server_id,
                "threat_type": "typosquat_detected",
                "evidence": f"Suspect: {suspect_name}, Levenshtein distance: {lev_dist}",
                "severity": "HIGH",
            }
            ws_write("mcp_threat_associations", [threat_row])
    
    return success


def get_legitimate_servers() -> List[Dict[str, Any]]:
    """Fetch legitimate server names from registry for comparison.
    
    Returns:
        List of server records with name, trust_score, registry_source
    """
    sql = """
    SELECT name, trust_score, registry_source, server_id
    FROM mcp_server_registry
    WHERE trust_score > 60
       OR registry_source = 'npm_official'
       OR (registry_source IS NULL AND (name LIKE '%server%' OR name LIKE '%mcp%' OR name LIKE '%context%'))
    """
    
    return ws_query(sql)


def process_server(server: Dict[str, Any]) -> None:
    """Process a single server for potential typo-squatting.
    
    Args:
        server: Server record with name, trust_score, etc.
    """
    name = server.get("name", "")
    if not name or len(name) < MIN_NAME_LENGTH:
        return
    
    if name.startswith("@"):
        return
    
    name_parts = name.replace("_", "-").lower().split("-")
    if len(name_parts) < 2:
        search_query = name
    else:
        search_query = "-".join(name_parts[:3])
    
    results = search_npm_registry(search_query, size=20)
    
    for result in results:
        suspect = result.get("name", "")
        
        if not suspect or suspect == name:
            continue
        
        if suspect.startswith("@"):
            continue
        
        if name in suspect or suspect in name:
            continue
        
        lev_dist = levenshtein(name.lower(), suspect.lower())
        
        if lev_dist > LEVENSHTEIN_THRESHOLD:
            if is_scope_variant(name, suspect) or check_homoglyph_similarity(name, suspect):
                pass
            else:
                continue
        
        downloads = get_npm_downloads(suspect)
        
        if downloads is not None and downloads >= MIN_DOWNLOADS_SUSPICIOUS:
            continue
        
        published_at = result.get("date")
        
        if not is_recent_publication(published_at, PUBLICATION_DAYS_SUSPICIOUS):
            if lev_dist > 1:
                continue
        
        create_typosquat_alert(
            suspect_name=suspect,
            target_name=name,
            lev_dist=lev_dist,
            downloads=downloads,
            published_at=published_at
        )


def check_single_instance() -> bool:
    """Ensure only one instance of this service is running.
    
    Returns:
        True if this is the only instance
    """
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    
    try:
        with open(pid_file, "r") as f:
            old_pid = int(f.read().strip())
        
        import os
        if os.path.exists(f"/proc/{old_pid}"):
            log.error(f"Another instance running with PID {old_pid}")
            return False
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass
    
    try:
        with open(pid_file, "w") as f:
            import os
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.error(f"Failed to create PID file: {e}")
        return False


def send_heartbeat() -> None:
    """Send heartbeat to service_health table."""
    sql = "SELECT last_heartbeat FROM service_health WHERE service = ?"
    results = ws_query(sql, {"p1": SERVICE_NAME})
    
    heartbeat_row = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
    }
    
    if results:
        ws_write("service_health", [heartbeat_row])
    else:
        try:
            requests.post(EXECUTE_URL, json={
                "sql": "CREATE TABLE IF NOT EXISTS service_health (id BIGINT PRIMARY KEY, service VARCHAR, last_heartbeat TIMESTAMPTZ)"
            }, timeout=5)
        except:
            pass
        ws_write("service_health", [heartbeat_row])


def run() -> None:
    """Main daemon loop for npm typo-squatting detection."""
    if not check_single_instance():
        log.error("Cannot start: another instance is running")
        return
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    
    log.info(f"Starting {SERVICE_NAME} daemon")
    
    ensure_tables()
    send_heartbeat()
    
    while True:
        try:
            cycle_start = time.time()
            
            log.info("Fetching legitimate servers for comparison...")
            servers = get_legitimate_servers()
            log.info(f"Processing {len(servers)} legitimate servers")
            
            for server in servers:
                try:
                    process_server(server)
                except Exception as e:
                    log.error(f"Error processing server {server.get('name')}: {e}")
            
            cycle_time = time.time() - cycle_start
            log.info(f"Typo-squat check cycle completed in {cycle_time:.1f}s")
            
        except Exception as e:
            log.error(f"Cycle error: {e}")
        
        send_heartbeat()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()