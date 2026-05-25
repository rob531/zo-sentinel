import requests
import logging
import time
from datetime import datetime
from typing import Dict, List, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SERVICE_NAME = "deduplicator"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 300
CYCLE_INTERVAL = 86400

def ws_query(sql: str, params: Optional[List] = None) -> List[Dict]:
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json().get("results", []) if isinstance(resp.json(), dict) else resp.json()
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []

def ws_write(table: str, rows: Any) -> bool:
    if isinstance(rows, dict):
        rows = [rows]
    payload = {"table": table, "rows": rows}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Write failed: {e}")
        return False

def send_heartbeat() -> bool:
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat()
    })

def compute_character_overlap(s1: str, s2: str) -> float:
    if not s1 or not s2:
        return 0.0
    s1_lower = s1.lower().strip()
    s2_lower = s2.lower().strip()
    if s1_lower == s2_lower:
        return 1.0
    set1 = set(s1_lower)
    set2 = set(s2_lower)
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0

def name_similarity(name1: str, name2: str) -> float:
    return compute_character_overlap(name1, name2)

def extract_npm_package(url: str) -> Optional[str]:
    if not url:
        return None
    import re
    patterns = [
        r'npmjs\.org/package/([^/]+)',
        r'npm\.im/([^/]+)',
        r'yarnpkg\.com/package/([^/]+)',
        r'registry\.npmjs\.org/([^/]+)',
        r'/node_modules/([^/]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None

def find_duplicates() -> Dict[str, List[Dict]]:
    servers = ws_query("""
        SELECT server_id, name, url, scan_count, trust_score, verdict
        FROM mcp_server_registry
        ORDER BY scan_count DESC
    """)
    if not servers:
        return {}
    
    url_groups: Dict[str, List[Dict]] = {}
    for srv in servers:
        url = srv.get("url") or ""
        if url not in url_groups:
            url_groups[url] = []
        url_groups[url].append(srv)
    
    name_groups: Dict[str, List[Dict]] = {}
    for srv in servers:
        name = (srv.get("name") or "").lower().strip()
        if name:
            if name not in name_groups:
                name_groups[name] = []
            name_groups[name].append(srv)
    
    npm_groups: Dict[str, List[Dict]] = {}
    for srv in servers:
        pkg = extract_npm_package(srv.get("url") or "")
        if pkg:
            if pkg not in npm_groups:
                npm_groups[pkg] = []
            npm_groups[pkg].append(srv)
    
    canonical_map: Dict[str, str] = {}
    
    for url, group in url_groups.items():
        if len(group) > 1:
            canonical = group[0]
            for dup in group[1:]:
                canonical_map[dup["server_id"]] = canonical["server_id"]
    
    for name, group in name_groups.items():
        if len(group) > 1:
            canonical = group[0]
            for dup in group[1:]:
                sid = dup["server_id"]
                if sid not in canonical_map:
                    canonical_map[sid] = canonical["server_id"]
    
    for pkg, group in npm_groups.items():
        if len(group) > 1:
            canonical = group[0]
            for dup in group[1:]:
                sid = dup["server_id"]
                if sid not in canonical_map:
                    canonical_map[sid] = canonical["server_id"]
    
    remaining_servers = [s for s in servers if s["server_id"] not in canonical_map]
    for i, srv1 in enumerate(remaining_servers):
        name1 = srv1.get("name") or ""
        for srv2 in remaining_servers[i+1:]:
            name2 = srv2.get("name") or ""
            if name_similarity(name1, name2) > 0.9:
                sid2 = srv2["server_id"]
                if sid2 not in canonical_map:
                    canonical_map[sid2] = srv1["server_id"]
                break
    
    dup_groups: Dict[str, List[Dict]] = {}
    for dup_sid, can_sid in canonical_map.items():
        if can_sid not in dup_groups:
            dup_groups[can_sid] = []
        dup_srv = next((s for s in servers if s["server_id"] == dup_sid), None)
        if dup_srv:
            dup_groups[can_sid].append(dup_srv)
    
    return dup_groups

def merge_signal_scores(canonical_id: str, dup_ids: List[str]) -> int:
    merged = 0
    existing = ws_query(f"""
        SELECT signal_name FROM mcp_signal_scores WHERE server_id = '{canonical_id}'
    """)
    existing_signals = {(r.get("signal_name") or "").lower() for r in existing}
    for dup_id in dup_ids:
        scores = ws_query(f"""
            SELECT signal_name, score, evidence FROM mcp_signal_scores
            WHERE server_id = '{dup_id}'
        """)
        for sc in scores:
            sig_name = sc.get("signal_name") or ""
            if sig_name.lower() not in existing_signals:
                ws_write("mcp_signal_scores", {
                    "server_id": canonical_id,
                    "signal_name": sig_name,
                    "score": sc.get("score"),
                    "evidence": sc.get("evidence")
                })
                existing_signals.add(sig_name.lower())
                merged += 1
    return merged

def merge_threat_associations(canonical_id: str, dup_ids: List[str]) -> int:
    merged = 0
    existing = ws_query(f"""
        SELECT threat_type FROM mcp_threat_associations WHERE server_id = '{canonical_id}'
    """)
    existing_threats = {(r.get("threat_type") or "").lower() for r in existing}
    for dup_id in dup_ids:
        threats = ws_query(f"""
            SELECT threat_type, evidence, severity FROM mcp_threat_associations
            WHERE server_id = '{dup_id}'
        """)
        for th in threats:
            th_type = th.get("threat_type") or ""
            if th_type.lower() not in existing_threats:
                ws_write("mcp_threat_associations", {
                    "server_id": canonical_id,
                    "threat_type": th_type,
                    "evidence": th.get("evidence"),
                    "severity": th.get("severity")
                })
                existing_threats.add(th_type.lower())
                merged += 1
    return merged

def delete_duplicate_servers(dup_ids: List[str]) -> int:
    deleted = 0
    for dup_id in dup_ids:
        ws_query(f"DELETE FROM mcp_signal_scores WHERE server_id = '{dup_id}'")
        ws_query(f"DELETE FROM mcp_threat_associations WHERE server_id = '{dup_id}'")
        ws_query(f"DELETE FROM mcp_definition_history WHERE server_id = '{dup_id}'")
        ws_query(f"DELETE FROM mcp_server_registry WHERE server_id = '{dup_id}'")
        deleted += 1
    return deleted

def write_dedup_report(actions: List[Dict[str, Any]], total_deduped: int) -> None:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# DEDUP_REPORT.md",
        f"",
        f"Generated: {timestamp}",
        f"",
        f"## Summary",
        f"- Total deduplication groups processed: {len(actions)}",
        f"- Total duplicate servers removed: {total_deduped}",
        f"",
    ]
    for i, action in enumerate(actions, 1):
        lines.append(f"## Group {i}")
        lines.append(f"- **Canonical server_id**: `{action['canonical_id']}`")
        lines.append(f"  - Name: {action.get('canonical_name', 'N/A')}")
        lines.append(f"  - URL: {action.get('canonical_url', 'N/A')}")
        lines.append(f"- **Duplicates merged**: {action['duplicate_count']}")
        lines.append(f"  - Duplicate IDs: {', '.join(action.get('duplicate_ids', []))}")
        lines.append(f"- **Signal scores merged**: {action['signals_merged']}")
        lines.append(f"- **Threat associations merged**: {action['threats_merged']}")
        lines.append("")
    try:
        with open("DEDUP_REPORT.md", "w") as f:
            f.write("\n".join(lines))
        logger.info(f"Wrote DEDUP_REPORT.md")
    except Exception as e:
        logger.error(f"Failed to write DEDUP_REPORT.md: {e}")

def write_dedup_complete() -> bool:
    return ws_write("mesh_events", {
        "event_type": "dedup_complete",
        "timestamp": datetime.utcnow().isoformat(),
        "source": SERVICE_NAME
    })

def check_single_instance() -> bool:
    recent = ws_query("""
        SELECT id FROM mesh_events
        WHERE event_type = 'dedup_start'
        AND timestamp > NOW() - INTERVAL '5 minutes'
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    if recent:
        logger.info("Another deduplicator instance is running, skipping")
        return False
    ws_write("mesh_events", {
        "event_type": "dedup_start",
        "timestamp": datetime.utcnow().isoformat(),
        "source": SERVICE_NAME
    })
    return True

def run() -> None:
    logger.info(f"Starting {SERVICE_NAME} daemon - cycle every {CYCLE_INTERVAL}s")
    while True:
        try:
            send_heartbeat()
            if check_single_instance():
                dup_groups = find_duplicates()
                actions = []
                total_deduped = 0
                if not dup_groups:
                    logger.info("No duplicates found")
                else:
                    logger.info(f"Found {len(dup_groups)} duplicate groups")
                    for canonical_id, dups in dup_groups.items():
                        can_srv = ws_query(f"""
                            SELECT name, url FROM mcp_server_registry
                            WHERE server_id = '{canonical_id}'
                            LIMIT 1
                        """)
                        can_name = can_srv[0].get("name", "N/A") if can_srv else "N/A"
                        can_url = can_srv[0].get("url", "N/A") if can_srv else "N/A"
                        dup_ids = [d["server_id"] for d in dups]
                        signals_merged = merge_signal_scores(canonical_id, dup_ids)
                        threats_merged = merge_threat_associations(canonical_id, dup_ids)
                        deleted = delete_duplicate_servers(dup_ids)
                        total_deduped += deleted
                        actions.append({
                            "canonical_id": canonical_id,
                            "canonical_name": can_name,
                            "canonical_url": can_url,
                            "duplicate_count": deleted,
                            "duplicate_ids": dup_ids,
                            "signals_merged": signals_merged,
                            "threats_merged": threats_merged
                        })
                        logger.info(f"Merged {deleted} duplicates into {canonical_id}")
                    write_dedup_report(actions, total_deduped)
                write_dedup_complete()
                logger.info(f"Deduplication cycle complete. Removed {total_deduped} duplicate servers.")
            send_heartbeat()
        except Exception as e:
            logger.error(f"Error in deduplication cycle: {e}")
        logger.info(f"Sleeping {CYCLE_INTERVAL}s until next cycle")
        time.sleep(CYCLE_INTERVAL)

if __name__ == "__main__":
    run()