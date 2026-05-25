#!/usr/bin/env python3
"""
shodan_exposure_correlator.py -- ZO-SENTINEL Passive Shodan exposure correlator.
Correlates MCP servers with Shodan exposure data to assess risk.
Polls every 86400s with heartbeat monitoring.
"""
import os
import socket
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from db_utils import ws_query, ws_write, ws_heartbeat

log = logging.getLogger(__name__)

SERVICE_NAME = "shodan_exposure_correlator"
SHODAN_API_KEY = os.environ.get("SHODAN_API_KEY", "")
SHODAN_API_URL = "https://api.shodan.io/shodan/host"
POLL_INTERVAL = 86400  # 24 hours

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"


def check_single_instance() -> bool:
    """Ensure only one instance runs at a time via PID file."""
    import os
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            return False
        except OSError:
            log.info(f"Stale PID file found, removing")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def get_servers_with_urls() -> List[Dict[str, Any]]:
    """Query registry for servers with resolvable URLs."""
    query = """
        SELECT server_id, name, url, trust_score, verdict
        FROM mcp_server_registry
        WHERE url IS NOT NULL AND url != ''
        ORDER BY last_seen DESC
    """
    try:
        result = ws_query(query)
        return result if result else []
    except Exception as e:
        log.error(f"Failed to query servers: {e}")
        return []


def resolve_hostname(url: str) -> Optional[str]:
    """Extract hostname from URL and resolve to IP."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname
        if hostname:
            ip = socket.gethostbyname(hostname)
            return ip
    except Exception as e:
        log.debug(f"Could not resolve {url}: {e}")
    return None


def query_shodan(ip_address: str) -> Optional[Dict[str, Any]]:
    """Query Shodan API for host exposure data."""
    if not SHODAN_API_KEY:
        log.warning("SHODAN_API_KEY not set, skipping Shodan query")
        return None
    
    url = f"{SHODAN_API_URL}/{ip_address}"
    params = {"key": SHODAN_API_KEY}
    
    try:
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            log.debug(f"No Shodan data for IP {ip_address}")
            return None
        else:
            log.warning(f"Shodan API returned {response.status_code} for {ip_address}")
            return None
    except requests.exceptions.RequestException as e:
        log.error(f"Shodan API request failed for {ip_address}: {e}")
        return None


def extract_cves(vulns: Optional[Dict[str, Any]]) -> List[str]:
    """Extract CVE IDs from Shodan vulnerability data."""
    if not vulns:
        return []
    return list(vulns.keys())


def calculate_exposure_score(
    open_ports: List[int],
    cves: List[str],
    vulns: Optional[Dict[str, Any]]
) -> int:
    """Calculate exposure score based on ports and CVEs."""
    score = 0
    
    # Critical CVE presence
    critical_cves = [c for c in cves if c.startswith("CVE-") and "CRITICAL" in str(vulns.get(c, ""))]
    score += min(len(critical_cves) * 15, 45)
    
    # Dangerous port exposures
    dangerous_ports = {
        2375: 30, 2376: 30,  # Docker socket
        6379: 25,           # Redis
        27017: 20,          # MongoDB
        5432: 15,           # PostgreSQL
        3306: 15,           # MySQL
        1433: 20,           # MSSQL
        9200: 20,           # Elasticsearch
    }
    
    for port in open_ports:
        if port in dangerous_ports:
            score += dangerous_ports[port]
        elif port == 22:
            score += 10  # Default SSH
    
    return min(score, 100)


def assess_severity(
    open_ports: List[int],
    cves: List[str],
    vulns: Optional[Dict[str, Any]]
) -> str:
    """Determine overall severity based on exposure."""
    critical_cves = [c for c in cves if c.startswith("CVE-") and "CRITICAL" in str(vulns.get(c, ""))]
    if critical_cves:
        return "CRITICAL"
    
    if 2375 in open_ports or 2376 in open_ports:
        return "CRITICAL"
    
    if 6379 in open_ports:
        return "HIGH"
    
    if 22 in open_ports and len(open_ports) <= 3:
        return "MEDIUM"
    
    if cves:
        return "MEDIUM"
    
    return "LOW"


def process_server(server: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Process a single server for Shodan exposure."""
    server_id = server["server_id"]
    url = server["url"]
    current_trust = server.get("trust_score", 50.0) or 50.0
    
    ip_address = resolve_hostname(url)
    if not ip_address:
        log.debug(f"Could not resolve IP for {server_id}")
        return None
    
    shodan_data = query_shodan(ip_address)
    if not shodan_data:
        return None
    
    open_ports = shodan_data.get("ports", [])
    vulns = shodan_data.get("vulns", {})
    cves = extract_cves(vulns)
    tags = shodan_data.get("tags", [])
    
    exposure_score = calculate_exposure_score(open_ports, cves, vulns)
    severity = assess_severity(open_ports, cves, vulns)
    
    # Adjust trust score based on exposure
    trust_adjustment = 0
    if severity == "CRITICAL":
        trust_adjustment = -20
    elif severity == "HIGH":
        trust_adjustment = -10
    elif severity == "MEDIUM":
        trust_adjustment = -5
    
    new_trust_score = max(0.0, min(100.0, current_trust + trust_adjustment))
    
    result = {
        "server_id": server_id,
        "ip_address": ip_address,
        "open_ports": json.dumps(open_ports),
        "cves_found": json.dumps(cves),
        "shodan_tags": json.dumps(tags),
        "exposure_score": exposure_score,
        "severity": severity,
        "trust_adjustment": trust_adjustment,
        "new_trust_score": new_trust_score,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    
    return result


def write_shodan_results(result: Dict[str, Any]) -> None:
    """Write Shodan scan results to database."""
    try:
        ws_write("shodan_results", {
            "server_id": result["server_id"],
            "ip_address": result["ip_address"],
            "open_ports": result["open_ports"],
            "cves_found": result["cves_found"],
            "shodan_tags": result["shodan_tags"],
            "exposure_score": result["exposure_score"],
            "severity": result["severity"],
            "scanned_at": result["scanned_at"],
        })
        log.info(f"Wrote shodan_results for {result['server_id']}")
    except Exception as e:
        log.error(f"Failed to write shodan_results: {e}")


def write_threat_association(result: Dict[str, Any]) -> None:
    """Write threat association if exposure score is high."""
    if result["exposure_score"] <= 60:
        return
    
    try:
        cves = json.loads(result["cves_found"])
        evidence = f"Exposure score: {result['exposure_score']}, Ports: {result['open_ports']}"
        if cves:
            evidence += f", CVEs: {', '.join(cves[:5])}"
        
        ws_write("mcp_threat_associations", {
            "server_id": result["server_id"],
            "threat_type": "shodan_exposure",
            "evidence": evidence,
            "severity": result["severity"],
            "reported_at": datetime.now(timezone.utc).isoformat(),
        })
        log.info(f"Wrote threat association for {result['server_id']} (score: {result['exposure_score']})")
    except Exception as e:
        log.error(f"Failed to write threat association: {e}")


def update_server_trust(server_id: str, new_trust_score: float) -> None:
    """Update trust score in mcp_server_registry."""
    try:
        ws_write("mcp_server_registry", {
            "server_id": server_id,
            "trust_score": new_trust_score,
            "last_assessed": datetime.now(timezone.utc).isoformat(),
        })
        log.info(f"Updated trust_score for {server_id} to {new_trust_score}")
    except Exception as e:
        log.error(f"Failed to update trust score: {e}")


def ensure_tables() -> None:
    """Ensure required tables exist."""
    create_shodan_results = """
        CREATE TABLE IF NOT EXISTS shodan_results (
            id BIGINT PRIMARY KEY,
            server_id VARCHAR,
            ip_address VARCHAR,
            open_ports TEXT,
            cves_found TEXT,
            shodan_tags TEXT,
            exposure_score INTEGER,
            severity VARCHAR,
            scanned_at TIMESTAMPTZ DEFAULT now()
        )
    """
    
    create_threat_associations = """
        CREATE TABLE IF NOT EXISTS mcp_threat_associations (
            id BIGINT PRIMARY KEY,
            server_id VARCHAR,
            threat_type VARCHAR,
            evidence TEXT,
            severity VARCHAR,
            reported_at TIMESTAMPTZ DEFAULT now()
        )
    """
    
    try:
        requests.post(EXECUTE_URL, json={"sql": create_shodan_results}, timeout=30)
        requests.post(EXECUTE_URL, json={"sql": create_threat_associations}, timeout=30)
        log.info("Ensured shodan_results and mcp_threat_associations tables exist")
    except Exception as e:
        log.error(f"Failed to create tables: {e}")


def cycle() -> int:
    """Run one correlation cycle."""
    log.info("Starting Shodan exposure correlation cycle")
    
    servers = get_servers_with_urls()
    log.info(f"Found {len(servers)} servers with URLs to check")
    
    processed = 0
    for server in servers:
        try:
            result = process_server(server)
            if result:
                write_shodan_results(result)
                write_threat_association(result)
                update_server_trust(result["server_id"], result["new_trust_score"])
                processed += 1
        except Exception as e:
            log.error(f"Error processing server {server.get('server_id')}: {e}")
    
    log.info(f"Shodan correlation cycle complete. Processed: {processed}/{len(servers)}")
    return processed


def run() -> None:
    """Main daemon loop."""
    if not check_single_instance():
        return
    
    ensure_tables()
    
    log.info(f"{SERVICE_NAME} starting - polling every {POLL_INTERVAL}s")
    
    while True:
        try:
            cycle()
            ws_heartbeat(SERVICE_NAME)
        except Exception as e:
            log.error(f"Error in main loop: {e}")
        
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
    )
    run()