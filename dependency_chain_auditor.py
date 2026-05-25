#!/usr/bin/env python3
"""
dependency_chain_auditor.py -- ZO-SENTINEL npm dependency chain auditor daemon.
Audits npm-sourced MCP servers for supply chain risks: malicious dependencies,
CVEs, dependency confusion, git:// URLs, and file:// paths.
Polls every 86400s. Heartbeats to write_service.
"""
import os
import sys
import time
import json
import logging
import hashlib
import requests
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

# Add project path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from known_threats import KNOWN_MALICIOUS_PACKAGES
except ImportError:
    KNOWN_MALICIOUS_PACKAGES = [
        "fake-postmark-mcp",
        "mcp-server-postmark-fake",
        "@mcp/server-postmark-clone",
        "mcp-whatsapp-stealer",
        "mcp-server-all",
        "@modelcontextprotocol/server-all",
    ]

log = logging.getLogger(__name__)

SERVICE_NAME = "dependency_chain_auditor"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 86400
NPM_REGISTRY = "https://registry.npmjs.org"
OSV_API = "https://api.osv.dev/v1/query"
PID_FILE = f"/tmp/zo_sentinel_{SERVICE_NAME}.pid"


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute query via query service."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write rows to table via write service."""
    if not rows:
        return {"status": "skipped", "reason": "no rows"}
    payload = {"table": table, "rows": rows}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> Dict[str, Any]:
    """Execute SQL via execute service."""
    payload = {"sql": sql}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat() -> bool:
    """Send heartbeat to write_service."""
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                "status": "running"
            }
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        log.warning("Heartbeat failed: %s", e)
        return False


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    pid = os.getpid()
    try:
        if os.path.exists(PID_FILE):
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            if old_pid != pid and os.path.exists(f"/proc/{old_pid}"):
                log.error("Another instance already running (PID %d)", old_pid)
                return False
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        return True
    except Exception as e:
        log.error("PID file check failed: %s", e)
        return False


def remove_pid_file() -> None:
    """Remove PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def ensure_tables() -> None:
    """Ensure required tables exist."""
    sqls = [
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
        """
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            id          BIGINT PRIMARY KEY,
            server_id   VARCHAR NOT NULL,
            signal_name VARCHAR NOT NULL,
            score       REAL,
            evidence    TEXT,
            scored_at   TIMESTAMPTZ DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dependency_audit_results (
            id              BIGINT PRIMARY KEY,
            server_id       VARCHAR NOT NULL,
            package_name    VARCHAR,
            dep_name        VARCHAR,
            dep_version     VARCHAR,
            threat_type     VARCHAR,
            evidence        TEXT,
            severity        VARCHAR,
            audited_at      TIMESTAMPTZ DEFAULT now()
        )
        """,
    ]
    for sql in sqls:
        try:
            ws_execute(sql)
        except Exception as e:
            log.warning("Table creation warning: %s", e)


def get_npm_sourced_servers() -> List[Dict[str, Any]]:
    """Get MCP servers sourced from npm registry."""
    sql = """
    SELECT server_id, name, registry_source, url, description
    FROM mcp_server_registry
    WHERE registry_source = 'npm'
       OR url LIKE '%npmjs.com%'
       OR name LIKE '@%/%'
       OR name LIKE '%mcp%'
    LIMIT 100
    """
    try:
        return ws_query(sql)
    except Exception as e:
        log.error("Failed to query servers: %s", e)
        return []


def extract_package_name(name: str) -> Optional[str]:
    """Extract package name from server name or URL."""
    if not name:
        return None
    if name.startswith("@"):
        parts = name.split("/")
        if len(parts) >= 2:
            return name
        return None
    if "/" in name:
        return name.split("/")[-1]
    return name


def fetch_npm_package(package_name: str, version: str = "latest") -> Optional[Dict[str, Any]]:
    """Fetch package metadata from npm registry."""
    try:
        url = f"{NPM_REGISTRY}/{package_name}/{version}"
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        log.warning("Failed to fetch npm package %s: %s", package_name, e)
        return None


def query_osv_cves(package_name: str) -> List[Dict[str, Any]]:
    """Query OSV.dev for CVEs affecting a package."""
    cves = []
    try:
        payload = {
            "package": {
                "name": package_name,
                "ecosystem": "npm"
            }
        }
        resp = requests.post(OSV_API, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            vulns = data.get("vulns", [])
            for v in vulns:
                cve_id = None
                for alias in v.get("aliases", []):
                    if alias.startswith("CVE-"):
                        cve_id = alias
                        break
                if not cve_id:
                    cve_id = v.get("id", "unknown")
                severity = "unknown"
                severity_lower = v.get("severity", [])
                for s in severity_lower:
                    if s.get("type") == "CVSS_V3":
                        severity = s.get("score", "unknown")
                        break
                cves.append({
                    "cve_id": cve_id,
                    "package": package_name,
                    "summary": v.get("summary", "")[:500],
                    "severity": severity,
                    "fixed_version": v.get("fixed", [None])[0] if v.get("fixed") else None,
                    "osv_id": v.get("id", "")
                })
    except Exception as e:
        log.warning("OSV query failed for %s: %s", package_name, e)
    return cves


def check_git_url_dependency(dep_value: str) -> bool:
    """Check if dependency version uses git:// URL."""
    if isinstance(dep_value, str):
        lower = dep_value.lower()
        if lower.startswith("git://") or lower.startswith("git+") or "github.com/" in lower:
            return True
        if lower.startswith("https://github.com/") or lower.startswith("git@github.com:"):
            return True
    return False


def check_file_url_dependency(dep_value: str) -> bool:
    """Check if dependency version uses file:// path."""
    if isinstance(dep_value, str):
        if dep_value.startswith("file://") or dep_value.startswith("file:"):
            return True
    return False


def check_dependency_confusion(dep_name: str, dep_value: str) -> bool:
    """Check for dependency confusion attack patterns."""
    if isinstance(dep_value, str):
        if dep_value.startswith("^") or dep_value.startswith("~"):
            try:
                ver = dep_value[1:]
                if not any(c.isdigit() for c in ver):
                    return True
            except Exception:
                pass
        if dep_value == "*" or dep_value == "latest":
            return True
        if dep_value.startswith("http://") and not dep_name.startswith("@"):
            return True
    return False


def is_malicious_package(package_name: str) -> bool:
    """Check if package is in known malicious list."""
    for malicious in KNOWN_MALICIOUS_PACKAGES:
        if malicious.startswith("@"):
            if package_name == malicious:
                return True
        else:
            if package_name == malicious or package_name.endswith("/" + malicious):
                return True
    return False


def audit_package_deps(server_id: str, package_name: str, depth: int = 0, max_depth: int = 2) -> Dict[str, Any]:
    """Recursively audit package dependencies."""
    results = {
        "malicious_deps": [],
        "cve_deps": [],
        "git_url_deps": [],
        "file_url_deps": [],
        "confusion_deps": [],
        "zero_download_deps": [],
        "audited": set(),
        "all_deps": {}
    }
    
    if depth > max_depth or not package_name:
        return results
    
    results["audited"].add(package_name)
    
    pkg_data = fetch_npm_package(package_name)
    if not pkg_data:
        if depth == 0:
            log.warning("Could not fetch package %s", package_name)
        return results
    
    deps = {}
    deps.update(pkg_data.get("dependencies", {}))
    deps.update(pkg_data.get("devDependencies", {}))
    
    results["all_deps"][package_name] = list(deps.keys())
    
    for dep_name, dep_version in deps.items():
        evidence_parts = [f"Dependency: {dep_name}@{dep_version}"]
        
        if is_malicious_package(dep_name):
            evidence_parts.append("MALICIOUS: in known_threats.KNOWN_MALICIOUS_PACKAGES")
            results["malicious_deps"].append({
                "package": dep_name,
                "version": dep_version,
                "evidence": "; ".join(evidence_parts)
            })
        
        cves = query_osv_cves(dep_name)
        if cves:
            for cve in cves:
                results["cve_deps"].append({
                    "package": dep_name,
                    "version": dep_version,
                    "cve_id": cve.get("cve_id", ""),
                    "severity": cve.get("severity", "unknown"),
                    "summary": cve.get("summary", "")
                })
        
        if check_git_url_dependency(dep_version):
            evidence_parts.append("GIT_URL: uses git:// URL (mutable commit)")
            results["git_url_deps"].append({
                "package": dep_name,
                "version": dep_version,
                "evidence": "; ".join(evidence_parts)
            })
        
        if check_file_url_dependency(dep_version):
            evidence_parts.append("FILE_URL: uses file:// path")
            results["file_url_deps"].append({
                "package": dep_name,
                "version": dep_version,
                "evidence": "; ".join(evidence_parts)
            })
        
        if check_dependency_confusion(dep_name, dep_version):
            evidence_parts.append("CONFUSION: potential dependency confusion")
            results["confusion_deps"].append({
                "package": dep_name,
                "version": dep_version,
                "evidence": "; ".join(evidence_parts)
            })
        
        if dep_version == "0.0.0" or dep_version == "0":
            results["zero_download_deps"].append({
                "package": dep_name,
                "version": dep_version,
                "evidence": "ZERO_VERSION: 0.0.0 or 0 (dependency confusion)"
            })
        
        if dep_name not in results["audited"]:
            transitive = audit_package_deps(server_id, dep_name, depth + 1, max_depth)
            for key in ["malicious_deps", "cve_deps", "git_url_deps", "file_url_deps", "confusion_deps", "zero_download_deps"]:
                results[key].extend(transitive.get(key, []))
            results["audited"].update(transitive.get("audited", set()))
            results["all_deps"].update(transitive.get("all_deps", {}))
    
    return results


def compute_signal_score(audit_results: Dict[str, Any]) -> float:
    """Compute supply_chain signal score based on audit findings."""
    score = 0.0
    
    score += len(audit_results.get("malicious_deps", [])) * 40.0
    score += len(audit_results.get("cve_deps", [])) * 5.0
    score += len(audit_results.get("git_url_deps", [])) * 10.0
    score += len(audit_results.get("file_url_deps", [])) * 10.0
    score += len(audit_results.get("confusion_deps", [])) * 15.0
    score += len(audit_results.get("zero_download_deps", [])) * 20.0
    
    total_deps = sum(len(d) for d in audit_results.get("all_deps", {}).values())
    if total_deps > 100:
        score += 5.0
    
    return min(score, 100.0)


def generate_evidence_summary(audit_results: Dict[str, Any]) -> str:
    """Generate human-readable evidence summary."""
    lines = []
    
    malicious = audit_results.get("malicious_deps", [])
    if malicious:
        lines.append(f"Malicious deps: {len(malicious)}")
        for m in malicious[:3]:
            lines.append(f"  - {m['package']}@{m['version']}")
    
    cves = audit_results.get("cve_deps", [])
    if cves:
        lines.append(f"CVEs found: {len(cves)}")
        for c in cves[:3]:
            lines.append(f"  - {c['cve_id']} ({c['severity']})")
    
    git = audit_results.get("git_url_deps", [])
    if git:
        lines.append(f"Git URL deps: {len(git)}")
    
    file_url = audit_results.get("file_url_deps", [])
    if file_url:
        lines.append(f"File URL deps: {len(file_url)}")
    
    confusion = audit_results.get("confusion_deps", [])
    if confusion:
        lines.append(f"Confusion deps: {len(confusion)}")
    
    zero = audit_results.get("zero_download_deps", [])
    if zero:
        lines.append(f"Zero-version deps: {len(zero)}")
    
    return " | ".join(lines) if lines else "No supply chain issues detected"


def record_threats(server_id: str, audit_results: Dict[str, Any]) -> None:
    """Record threat associations to write_service."""
    threats = []
    
    for m in audit_results.get("malicious_deps", []):
        threats.append({
            "server_id": server_id,
            "threat_type": "malicious_dependency",
            "evidence": m.get("evidence", ""),
            "severity": "critical"
        })
    
    for c in audit_results.get("cve_deps", []):
        threats.append({
            "server_id": server_id,
            "threat_type": "cve_dependency",
            "evidence": f"{c.get('cve_id', '')}: {c.get('summary', '')}",
            "severity": c.get("severity", "medium")
        })
    
    for g in audit_results.get("git_url_deps", []):
        threats.append({
            "server_id": server_id,
            "threat_type": "git_url_dependency",
            "evidence": g.get("evidence", ""),
            "severity": "high"
        })
    
    for f in audit_results.get("file_url_deps", []):
        threats.append({
            "server_id": server_id,
            "threat_type": "file_url_dependency",
            "evidence": f.get("evidence", ""),
            "severity": "medium"
        })
    
    for conf in audit_results.get("confusion_deps", []):
        threats.append({
            "server_id": server_id,
            "threat_type": "dependency_confusion",
            "evidence": conf.get("evidence", ""),
            "severity": "high"
        })
    
    for z in audit_results.get("zero_download_deps", []):
        threats.append({
            "server_id": server_id,
            "threat_type": "zero_version_dependency",
            "evidence": z.get("evidence", ""),
            "severity": "medium"
        })
    
    if threats:
        try:
            ws_write("mcp_threat_associations", threats)
            log.info("Recorded %d threats for server %s", len(threats), server_id)
        except Exception as e:
            log.error("Failed to write threats: %s", e)


def record_signal_score(server_id: str, score: float, evidence: str) -> None:
    """Record supply_chain signal score to write_service."""
    try:
        ws_write("mcp_signal_scores", [{
            "server_id": server_id,
            "signal_name": "supply_chain",
            "score": score,
            "evidence": evidence
        }])
        log.info("Recorded supply_chain score %.1f for %s", score, server_id)
    except Exception as e:
        log.error("Failed to write signal score: %s", e)


def cycle() -> Dict[str, Any]:
    """Run one audit cycle."""
    log.info("Starting dependency audit cycle")
    
    servers = get_npm_sourced_servers()
    log.info("Found %d npm-sourced servers to audit", len(servers))
    
    results_summary = {
        "servers_audited": 0,
        "threats_found": 0,
        "scores_recorded": 0,
        "errors": []
    }
    
    for server in servers:
        server_id = server.get("server_id", "")
        name = server.get("name", "")
        
        if not server_id:
            continue
        
        try:
            package_name = extract_package_name(name)
            if not package_name:
                log.debug("Cannot extract package name from %s", name)
                continue
            
            log.info("Auditing %s (package: %s)", server_id, package_name)
            
            audit_results = audit_package_deps(server_id, package_name, max_depth=2)
            
            total_threats = (
                len(audit_results.get("malicious_deps", [])) +
                len(audit_results.get("cve_deps", [])) +
                len(audit_results.get("git_url_deps", [])) +
                len(audit_results.get("file_url_deps", [])) +
                len(audit_results.get("confusion_deps", [])) +
                len(audit_results.get("zero_download_deps", []))
            )
            
            if total_threats > 0:
                record_threats(server_id, audit_results)
                results_summary["threats_found"] += total_threats
            
            signal_score = compute_signal_score(audit_results)
            evidence = generate_evidence_summary(audit_results)
            record_signal_score(server_id, signal_score, evidence)
            
            results_summary["scores_recorded"] += 1
            results_summary["servers_audited"] += 1
            
        except Exception as e:
            log.error("Error auditing %s: %s", server_id, e)
            results_summary["errors"].append({"server_id": server_id, "error": str(e)})
    
    log.info("Audit cycle complete: %s", results_summary)
    return results_summary


def heartbeat_loop() -> None:
    """Background heartbeat thread."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run() -> None:
    """Main entry point for daemon."""
    if not check_single_instance():
        log.error("Cannot acquire lock, exiting")
        sys.exit(1)
    
    try:
        ensure_tables()
        log.info("%s starting", SERVICE_NAME)
        send_heartbeat()
        
        import threading
        hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        hb_thread.start()
        
        while True:
            try:
                cycle()
            except Exception as e:
                log.error("Cycle failed: %s", e)
            
            log.info("Sleeping %d seconds until next audit", POLL_INTERVAL)
            time.sleep(POLL_INTERVAL)
    
    finally:
        remove_pid_file()
        log.info("%s stopped", SERVICE_NAME)


if __name__ == "__main__":
    run()