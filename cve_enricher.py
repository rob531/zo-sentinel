#!/usr/bin/env python3
"""
cve_enricher.py -- ZO-SENTINEL CVE enrichment daemon.
Every 21600s: fetches recent CVEs from NVD API related to MCP packages,
matches against mcp_server_registry, and writes threat associations.
"""
import os
import sys
import time
import json
import logging
import hashlib
import re
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import requests

# Configuration
SERVICE_NAME = "cve_enricher"
PORT = 8782
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8773/query"
HEARTBEAT_INTERVAL = 300
CYCLE_INTERVAL = 21600
NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_RESULTS_PER_PAGE = 50
LOG_FILE = "CVE_ENRICHMENT_LOG.md"
PID_FILE = f"/tmp/zo_sentinel_{SERVICE_NAME}.pid"

log = logging.getLogger(__name__)


def get_write_url() -> str:
    return WRITE_SERVICE_URL


def get_query_url() -> str:
    return QUERY_URL


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute query via inference_router."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(get_query_url(), json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok" or data.get("success"):
            return data.get("data", [])
        log.warning(f"Query returned non-ok status: {data}")
        return []
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write to write_service using 'rows' field (not 'row')."""
    if isinstance(rows, dict):
        rows = [rows]
    payload = {"table": table, "rows": rows}
    try:
        resp = requests.post(get_write_url(), json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok" or data.get("success"):
            return True
        log.warning(f"Write failed: {data}")
        return False
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(service_name: str) -> bool:
    """Send heartbeat to service_health table."""
    return ws_write("service_health", {
        "service": service_name,
        "last_heartbeat": datetime.utcnow().isoformat()
    })


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            log.info("Stale PID file found, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Clean up PID file on exit."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass


def compute_similarity(a: str, b: str) -> float:
    """Simple string similarity using Jaccard on lowercase alphanumeric tokens."""
    def tokenize(s: str) -> set:
        return set(re.findall(r'[a-z0-9]+', s.lower()))
    if not a or not b:
        return 0.0
    t1, t2 = tokenize(a), tokenize(b)
    if not t1 or not t2:
        return 0.0
    intersection = len(t1 & t2)
    union = len(t1 | t2)
    return intersection / union if union > 0 else 0.0


def get_cves_from_nvd(keyword: str = "mcp model context protocol") -> List[Dict[str, Any]]:
    """Fetch CVEs from NVD API."""
    params = {
        "keywordSearch": keyword,
        "resultsPerPage": NVD_RESULTS_PER_PAGE,
    }
    headers = {"apiKey": os.environ.get("NVD_API_KEY", "")}
    try:
        resp = requests.get(NVD_API_URL, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        vulnerabilities = data.get("vulnerabilities", [])
        cves = []
        for vuln in vulnerabilities:
            cve_data = vuln.get("cve", {})
            cve_id = cve_data.get("id", "")
            descriptions = cve_data.get("descriptions", [])
            description = ""
            for desc in descriptions:
                if desc.get("lang") == "en":
                    description = desc.get("value", "")
                    break
            if not description:
                for desc in descriptions:
                    description = desc.get("value", "")
                    break
            metrics = cve_data.get("metrics", {})
            cvss_v31 = metrics.get("cvssMetricV31", [])
            cvss_v30 = metrics.get("cvssMetricV30", [])
            cvss_v2 = metrics.get("cvssMetricV2", [])
            cvss_score = 0.0
            if cvss_v31:
                cvss_score = cvss_v31[0].get("cvssData", {}).get("baseScore", 0.0)
            elif cvss_v30:
                cvss_score = cvss_v30[0].get("cvssData", {}).get("baseScore", 0.0)
            elif cvss_v2:
                cvss_score = cvss_v2[0].get("cvssData", {}).get("baseScore", 0.0)
            configurations = cve_data.get("configurations", [])
            affected_packages = []
            for config in configurations:
                for node in config.get("nodes", []):
                    for cpe_match in node.get("cpeMatches", []):
                        criteria = cpe_match.get("criteria", "")
                        match = re.search(r'cpe:2\.3:a:([^:]+):([^:]+):', criteria)
                        if match:
                            vendor = match.group(1)
                            package = match.group(2)
                            affected_packages.append(f"{vendor}:{package}")
            cves.append({
                "cve_id": cve_id,
                "description": description[:500],
                "cvss_score": cvss_score,
                "severity": severity_from_cvss(cvss_score),
                "affected_packages": affected_packages,
                "published": cve_data.get("published", ""),
                "last_modified": cve_data.get("lastModified", ""),
            })
        return cves
    except Exception as e:
        log.error(f"Failed to fetch CVEs from NVD: {e}")
        return []


def severity_from_cvss(cvss_score: float) -> str:
    """Convert CVSS score to severity level."""
    if cvss_score >= 9.0:
        return "CRITICAL"
    elif cvss_score >= 7.0:
        return "HIGH"
    elif cvss_score >= 4.0:
        return "MEDIUM"
    else:
        return "LOW"


def get_servers_from_registry() -> List[Dict[str, Any]]:
    """Get all servers from registry."""
    sql = "SELECT server_id, name, url FROM mcp_server_registry"
    return ws_query(sql)


def match_cve_to_servers(cve: Dict[str, Any], servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Match CVE affected packages to servers by name similarity."""
    affected = cve.get("affected_packages", [])
    cve_name = cve.get("cve_id", "")
    matches = []
    for server in servers:
        server_id = server.get("server_id", "")
        name = server.get("name", "") or ""
        url = server.get("url", "") or ""
        best_similarity = 0.0
        best_match_name = ""
        for pkg in affected:
            pkg_lower = pkg.lower()
            for server_candidate in [name, url]:
                if not server_candidate:
                    continue
                sim = compute_similarity(server_candidate, pkg)
                if sim > best_similarity:
                    best_similarity = sim
                    best_match_name = server_candidate
        if best_similarity >= 0.25:
            matches.append({
                "server_id": server_id,
                "similarity": best_similarity,
                "matched_name": best_match_name,
            })
            log.info(f"CVE {cve['cve_id']} matched server {server_id} (sim={best_similarity:.2f})")
    return matches


def extract_new_patterns(cve: Dict[str, Any]) -> List[str]:
    """Extract potential new injection patterns from CVE description."""
    patterns = []
    description = cve.get("description", "").lower()
    pattern_pairs = [
        (r"<important>", "CVE detected <IMPORTANT> tag"),
        (r"ignore previous", "CVE detected ignore instruction pattern"),
        (r"base64.*encoded", "CVE detected base64 encoding pattern"),
        (r"exfiltrat", "CVE detected exfiltration keyword"),
        (r"~/.ssh", "CVE detected SSH key access pattern"),
        (r"\\.env.*secret", "CVE detected env secret pattern"),
    ]
    for pattern, _ in pattern_pairs:
        if re.search(pattern, description):
            for _, label in pattern_pairs:
                if label not in patterns:
                    patterns.append(label)
    return patterns


def write_threat_association(server_id: str, cve: Dict[str, Any]) -> bool:
    """Write threat association to mcp_threat_associations table."""
    evidence = f"{cve['cve_id']}: {cve['description'][:200]}"
    return ws_write("mcp_threat_associations", {
        "server_id": server_id,
        "threat_type": "cve",
        "evidence": evidence,
        "severity": cve["severity"],
        "reported_at": datetime.utcnow().isoformat(),
    })


def check_existing_association(server_id: str, cve_id: str) -> bool:
    """Check if association already exists."""
    sql = f"SELECT id FROM mcp_threat_associations WHERE server_id = '{server_id}' AND threat_type = 'cve' AND evidence LIKE '{cve_id}%'"
    results = ws_query(sql)
    return len(results) > 0


def ensure_tables():
    """Ensure required tables exist."""
    tables = [
        """
        CREATE TABLE IF NOT EXISTS mcp_threat_associations (
            id          BIGINT PRIMARY KEY,
            server_id   VARCHAR NOT NULL,
            threat_type VARCHAR,
            evidence    TEXT,
            severity    VARCHAR,
            reported_at TIMESTAMPTZ DEFAULT now()
        )
        """,
    ]
    for sql in tables:
        try:
            requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        except Exception as e:
            log.warning(f"Table creation warning: {e}")


def write_log_entry(cve: Dict[str, Any], matched_servers: List[Dict[str, Any]], new_patterns: List[str]):
    """Append entry to CVE_ENRICHMENT_LOG.md."""
    timestamp = datetime.utcnow().isoformat()
    matched_ids = [s["server_id"] for s in matched_servers]
    log_lines = [
        f"## {cve['cve_id']} @ {timestamp}",
        f"- **Severity**: {cve['severity']} (CVSS: {cve['cvss_score']})",
        f"- **Description**: {cve['description'][:300]}",
        f"- **Affected Packages**: {', '.join(cve['affected_packages'][:10]) or 'N/A'}",
        f"- **Matched Servers**: {', '.join(matched_ids) if matched_ids else 'None'}",
        f"- **New Patterns**: {', '.join(new_patterns) if new_patterns else 'None'}",
        "",
    ]
    with open(LOG_FILE, "a") as f:
        f.write("\n".join(log_lines))


def initialize_log():
    """Initialize CVE_ENRICHMENT_LOG.md header."""
    header = f"# CVE Enrichment Log\nGenerated: {datetime.utcnow().isoformat()}\n---\n"
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            f.write(header)


def cycle() -> Dict[str, Any]:
    """Run one enrichment cycle."""
    log.info("Starting CVE enrichment cycle")
    stats = {
        "cves_fetched": 0,
        "servers_matched": 0,
        "associations_written": 0,
        "new_patterns": [],
        "errors": [],
    }
    try:
        cves = get_cves_from_nvd()
        stats["cves_fetched"] = len(cves)
        log.info(f"Fetched {len(cves)} CVEs from NVD")
        servers = get_servers_from_registry()
        log.info(f"Found {len(servers)} servers in registry")
        for cve in cves:
            matches = match_cve_to_servers(cve, servers)
            for match in matches:
                server_id = match["server_id"]
                if check_existing_association(server_id, cve["cve_id"]):
                    log.debug(f"Association already exists for {server_id} and {cve['cve_id']}")
                    continue
                if write_threat_association(server_id, cve):
                    stats["associations_written"] += 1
                    stats["servers_matched"] += 1
            new_patterns = extract_new_patterns(cve)
            if new_patterns:
                stats["new_patterns"].extend(new_patterns)
            write_log_entry(cve, matches, new_patterns)
        log.info(f"Cycle complete: {stats}")
        return stats
    except Exception as e:
        log.error(f"Cycle failed: {e}")
        stats["errors"].append(str(e))
        return stats


def heartbeat_loop():
    """Background heartbeat thread."""
    while True:
        send_heartbeat(SERVICE_NAME)
        time.sleep(HEARTBEAT_INTERVAL)


import threading


def run():
    """Main entry point for daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if not check_single_instance():
        log.error("Cannot acquire lock, exiting")
        sys.exit(1)
    try:
        log.info(f"Starting {SERVICE_NAME} daemon")
        ensure_tables()
        initialize_log()
        hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        hb_thread.start()
        while True:
            cycle()
            log.info(f"Sleeping for {CYCLE_INTERVAL}s until next cycle")
            time.sleep(CYCLE_INTERVAL)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()