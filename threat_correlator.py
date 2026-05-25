#!/usr/bin/env python3
"""
threat_correlator.py -- ZO-SENTINEL Threat Correlation Daemon
Every 43200s: finds clusters of related threats across servers.
Correlates threat data to identify supply chain compromises, coordinated
vulnerabilities, and organizational risk patterns.
"""
import os
import sys
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
SERVICE_NAME = "threat_correlator"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
CYCLE_INTERVAL = 43200  # 12 hours

# Correlation thresholds
MIN_CLUSTER_SIZE = 3
VERDICT_HIGH_RISK = "HIGH_RISK_ISOLATED"


def get_write_url() -> str:
    """Get write service URL from environment or use default."""
    return os.environ.get("WRITE_SERVICE_URL", WRITE_SERVICE_URL)


def get_execute_url() -> str:
    """Get execute service URL from environment or use default."""
    return os.environ.get("EXECUTE_URL", EXECUTE_URL)


def get_query_url() -> str:
    """Get query service URL from environment or use default."""
    return os.environ.get("QUERY_URL", QUERY_URL)


def send_heartbeat(write_url: str, service_name: str) -> bool:
    """Send heartbeat to write service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": service_name,
                "last_heartbeat": datetime.utcnow().isoformat(),
                "status": "running"
            }
        }
        resp = requests.post(write_url, json=payload, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Heartbeat failed: {e}")
        return False


def ws_query(query: str) -> List[Dict[str, Any]]:
    """Execute query and return results."""
    try:
        resp = requests.post(get_query_url(), json={"sql": query}, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        return []
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write rows to specified table via write_service."""
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(get_write_url(), json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Write failed for {table}: {e}")
        return False


def check_single_instance(pid_file: str) -> bool:
    """Ensure only one instance is running."""
    pid = os.getpid()
    if os.path.exists(pid_file):
        old_pid = int(open(pid_file).read().strip())
        try:
            os.kill(old_pid, 0)
            logger.error(f"Another instance (PID {old_pid}) is already running.")
            return False
        except OSError:
            pass
    with open(pid_file, "w") as f:
        f.write(str(pid))
    return True


def remove_pid_file(pid_file: str) -> None:
    """Remove PID file on exit."""
    try:
        if os.path.exists(pid_file):
            os.remove(pid_file)
    except Exception:
        pass


def get_all_threats() -> List[Dict[str, Any]]:
    """Fetch all threat associations for correlation analysis."""
    query = """
    SELECT 
        ta.id,
        ta.server_id,
        ta.threat_type,
        ta.evidence,
        ta.severity,
        ta.reported_at,
        r.name as server_name,
        r.npm_author,
        r.github_org,
        r.verdict,
        r.trust_score
    FROM mcp_threat_associations ta
    LEFT JOIN mcp_server_registry r ON ta.server_id = r.server_id
    ORDER BY ta.reported_at DESC
    """
    return ws_query(query)


def find_supply_chain_clusters(threats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Find clusters where 3+ servers share same npm author AND threat_type=tool_mutation.
    Returns list of correlated threat events.
    """
    # Group by npm author
    author_groups: Dict[str, List[Dict]] = {}
    for threat in threats:
        if threat.get("npm_author"):
            author = threat["npm_author"]
            if author not in author_groups:
                author_groups[author] = []
            author_groups[author].append(threat)
    
    clusters = []
    for author, group in author_groups.items():
        # Count unique servers with tool_mutation threat
        tool_mutation_servers = set()
        for threat in group:
            if threat.get("threat_type") == "tool_mutation":
                tool_mutation_servers.add(threat.get("server_id"))
        
        if len(tool_mutation_servers) >= MIN_CLUSTER_SIZE:
            clusters.append({
                "type": "supply_chain_compromise",
                "trigger": f"{len(tool_mutation_servers)}+ servers with tool_mutation from npm author '{author}'",
                "affected_servers": list(tool_mutation_servers),
                "npm_author": author,
                "severity": "CRITICAL",
                "description": f"Supply chain compromise detected: npm author '{author}' has {len(tool_mutation_servers)} servers exhibiting tool mutation behavior. This suggests the npm package or author may be compromised."
            })
    
    return clusters


def find_coordinated_vulnerabilities(threats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Find servers affected by the same CVE.
    Returns list of correlated vulnerability events.
    """
    # Extract CVE patterns from evidence
    cve_groups: Dict[str, List[Dict]] = {}
    for threat in threats:
        evidence = threat.get("evidence", "")
        # Look for CVE pattern in evidence
        if "CVE-" in evidence or "CVE-" in threat.get("severity", ""):
            # Extract CVE ID
            import re
            cve_matches = re.findall(r'CVE-\d{4}-\d+', evidence + threat.get("severity", ""))
            for cve in cve_matches:
                if cve not in cve_groups:
                    cve_groups[cve] = []
                cve_groups[cve].append(threat)
    
    clusters = []
    for cve, group in cve_groups.items():
        unique_servers = set(t.get("server_id") for t in group if t.get("server_id"))
        if len(unique_servers) >= 2:
            clusters.append({
                "type": "coordinated_vulnerability",
                "trigger": f"CVE {cve} affects {len(unique_servers)} servers",
                "affected_servers": list(unique_servers),
                "cve_id": cve,
                "severity": "HIGH",
                "description": f"Coordinated vulnerability detected: CVE {cve} is affecting {len(unique_servers)} servers. This indicates a coordinated attack exploiting a known vulnerability."
            })
    
    return clusters


def find_org_risk_patterns(threats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Find servers from same GitHub org with HIGH_RISK_ISOLATED verdict.
    Returns list of organizational risk pattern events.
    """
    # Group by GitHub org
    org_groups: Dict[str, List[Dict]] = {}
    for threat in threats:
        org = threat.get("github_org")
        if org and threat.get("verdict") == VERDICT_HIGH_RISK:
            if org not in org_groups:
                org_groups[org] = []
            org_groups[org].append(threat)
    
    clusters = []
    for org, group in org_groups.items():
        unique_servers = set(t.get("server_id") for t in group if t.get("server_id"))
        if len(unique_servers) >= 2:
            clusters.append({
                "type": "org_risk_pattern",
                "trigger": f"GitHub org '{org}' has {len(unique_servers)} HIGH_RISK_ISOLATED servers",
                "affected_servers": list(unique_servers),
                "github_org": org,
                "severity": "HIGH",
                "description": f"Organizational risk pattern detected: GitHub org '{org}' has {len(unique_servers)} servers with HIGH_RISK_ISOLATED verdict. This suggests a systematic issue with the organization's security practices or tooling."
            })
    
    return clusters


def write_correlation_events(clusters: List[Dict[str, Any]]) -> int:
    """Write correlated threat events to database."""
    written = 0
    for cluster in clusters:
        threat_type = f"correlated_{cluster['type']}"
        
        for server_id in cluster.get("affected_servers", []):
            event = {
                "server_id": server_id,
                "threat_type": threat_type,
                "evidence": cluster.get("description", ""),
                "severity": cluster.get("severity", "MEDIUM"),
                "correlation_type": cluster["type"],
                "correlation_trigger": cluster.get("trigger", ""),
                "related_servers": ",".join(cluster.get("affected_servers", [])),
                "reported_at": datetime.utcnow().isoformat()
            }
            
            if ws_write("mcp_threat_associations", event):
                written += 1
    
    return written


def generate_correlation_report(
    supply_chain: List[Dict],
    coordinated_vulns: List[Dict],
    org_patterns: List[Dict]
) -> str:
    """Generate markdown correlation report."""
    report = []
    report.append("# ZO-SENTINEL Threat Correlation Report")
    report.append(f"Generated: {datetime.utcnow().isoformat()}")
    report.append("")
    
    total_clusters = len(supply_chain) + len(coordinated_vulns) + len(org_patterns)
    report.append(f"## Summary")
    report.append(f"- Total correlation clusters: {total_clusters}")
    report.append(f"- Supply chain compromises: {len(supply_chain)}")
    report.append(f"- Coordinated vulnerabilities: {len(coordinated_vulns)}")
    report.append(f"- Organizational risk patterns: {len(org_patterns)}")
    report.append("")
    
    if supply_chain:
        report.append("## Supply Chain Compromises")
        for i, cluster in enumerate(supply_chain, 1):
            report.append(f"### {i}. {cluster['trigger']}")
            report.append(f"- **Severity:** {cluster['severity']}")
            report.append(f"- **Affected Servers:** {len(cluster['affected_servers'])}")
            report.append(f"- **Description:** {cluster['description']}")
            report.append("")
    
    if coordinated_vulns:
        report.append("## Coordinated Vulnerabilities")
        for i, cluster in enumerate(coordinated_vulns, 1):
            report.append(f"### {i}. {cluster['trigger']}")
            report.append(f"- **Severity:** {cluster['severity']}")
            report.append(f"- **CVE ID:** {cluster.get('cve_id', 'Unknown')}")
            report.append(f"- **Affected Servers:** {len(cluster['affected_servers'])}")
            report.append(f"- **Description:** {cluster['description']}")
            report.append("")
    
    if org_patterns:
        report.append("## Organizational Risk Patterns")
        for i, cluster in enumerate(org_patterns, 1):
            report.append(f"### {i}. {cluster['trigger']}")
            report.append(f"- **Severity:** {cluster['severity']}")
            report.append(f"- **GitHub Org:** {cluster.get('github_org', 'Unknown')}")
            report.append(f"- **Affected Servers:** {len(cluster['affected_servers'])}")
            report.append(f"- **Description:** {cluster['description']}")
            report.append("")
    
    return "\n".join(report)


def cycle() -> None:
    """Main correlation cycle."""
    logger.info("Starting threat correlation cycle...")
    write_url = get_write_url()
    
    # Send heartbeat
    send_heartbeat(write_url, SERVICE_NAME)
    
    # Fetch all threats
    threats = get_all_threats()
    logger.info(f"Fetched {len(threats)} threats for correlation analysis")
    
    if not threats:
        logger.warning("No threats found for correlation")
        return
    
    # Find correlation clusters
    supply_chain = find_supply_chain_clusters(threats)
    coordinated_vulns = find_coordinated_vulnerabilities(threats)
    org_patterns = find_org_risk_patterns(threats)
    
    total_clusters = len(supply_chain) + len(coordinated_vulns) + len(org_patterns)
    logger.info(f"Found {total_clusters} correlation clusters:")
    logger.info(f"  - Supply chain compromises: {len(supply_chain)}")
    logger.info(f"  - Coordinated vulnerabilities: {len(coordinated_vulns)}")
    logger.info(f"  - Org risk patterns: {len(org_patterns)}")
    
    # Write correlation events
    if total_clusters > 0:
        written = write_correlation_events(supply_chain + coordinated_vulns + org_patterns)
        logger.info(f"Wrote {written} correlated threat records")
    
    # Generate report
    report = generate_correlation_report(supply_chain, coordinated_vulns, org_patterns)
    report_path = os.path.join(os.path.dirname(__file__), "CORRELATION_REPORT.md")
    with open(report_path, "w") as f:
        f.write(report)
    logger.info(f"Correlation report written to {report_path}")
    
    # Final heartbeat
    send_heartbeat(write_url, SERVICE_NAME)
    logger.info("Threat correlation cycle completed")


def run() -> None:
    """Run the threat correlator daemon."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pid_file = os.path.join(script_dir, f"{SERVICE_NAME}.pid")
    
    if not check_single_instance(pid_file):
        logger.error("Failed to acquire lock. Exiting.")
        sys.exit(1)
    
    logger.info(f"Starting {SERVICE_NAME} daemon...")
    
    try:
        while True:
            cycle()
            logger.info(f"Sleeping for {CYCLE_INTERVAL} seconds...")
            time.sleep(CYCLE_INTERVAL)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        remove_pid_file(pid_file)


if __name__ == "__main__":
    run()