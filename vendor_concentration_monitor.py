#!/usr/bin/env python3
"""
vendor_concentration_monitor.py -- ZO-SENTINEL vendor concentration risk monitor.
Groups MCP servers by npm author/scope, GitHub org, or domain.
Detects concentration risk and single-vendor dependencies.
"""
import os
import re
import time
import logging
import requests
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urlparse

SERVICE_NAME = "vendor_concentration_monitor"
PORT = 8779
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8773"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300

log = logging.getLogger(__name__)

def ws_query(sql: str, params: Optional[List] = None) -> List[Dict[str, Any]]:
    """Execute a query via inference_router."""
    try:
        response = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql, "params": params or []},
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            return result.get("data", [])
        log.warning(f"Query failed: {response.status_code} {response.text}")
        return []
    except Exception as e:
        log.error(f"Query error: {e}")
        return []

def ws_write(table: str, rows: Any) -> bool:
    """Write rows to write_service using 'rows' not 'row'."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        return response.status_code == 200
    except Exception as e:
        log.error(f"Write error: {e}")
        return False

def get_write_url() -> str:
    return WRITE_SERVICE_URL

def send_heartbeat() -> bool:
    """Send heartbeat to write_service."""
    try:
        return ws_write("service_health", {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat(),
            "status": "running"
        })
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")
        return False

def check_single_instance(pid_file: str) -> bool:
    """Ensure only one instance runs."""
    import os
    pid = os.getpid()
    if os.path.exists(pid_file):
        old_pid = int(open(pid_file).read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance running (PID {old_pid})")
            return False
        except OSError:
            pass
    with open(pid_file, "w") as f:
        f.write(str(pid))
    return True

def remove_pid_file(pid_file: str):
    """Remove PID file on exit."""
    try:
        os.remove(pid_file)
    except Exception:
        pass

def extract_npm_vendor(url: str) -> Optional[str]:
    """Extract npm author/scope from URL."""
    patterns = [
        r'npmjs\.org/(?:package/)?(@[^/]+/[^\s]+)',
        r'npmjs\.com/(?:package/)?(@[^/]+/[^\s]+)',
        r'registry\.npmjs\.org/(@[^/]+)/',
        r'github\.com/([^/]+)/.*npm',
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            vendor = match.group(1)
            if vendor.startswith('@'):
                scope = vendor.split('/')[0] if '/' in vendor else vendor
                return scope
            return vendor
    return None

def extract_github_org(url: str) -> Optional[str]:
    """Extract GitHub organization from URL."""
    patterns = [
        r'github\.com/([^/]+)/',
        r'raw\.githubusercontent\.com/([^/]+)/',
        r'api\.github\.com/repos/([^/]+)/',
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            return match.group(1).lower()
    return None

def extract_domain(url: str) -> Optional[str]:
    """Extract domain/hostname from URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if parsed.netloc:
            domain = parsed.netloc.lower()
            domain = re.sub(r'^www\.', '', domain)
            return domain
    except Exception:
        pass
    return None

def determine_vendor_type(url: str) -> Tuple[Optional[str], str]:
    """Determine vendor identifier and type from URL."""
    npm_vendor = extract_npm_vendor(url)
    if npm_vendor:
        return (npm_vendor, "npm_scope" if npm_vendor.startswith('@') else "npm_author")
    
    github_org = extract_github_org(url)
    if github_org:
        return (github_org, "github_org")
    
    domain = extract_domain(url)
    if domain:
        return (domain, "domain")
    
    return (None, "unknown")

def get_approved_servers() -> List[Dict[str, Any]]:
    """Fetch all APPROVED servers from registry."""
    sql = """
    SELECT server_id, name, url, registry_source, trust_score, verdict, last_assessed
    FROM mcp_server_registry
    WHERE verdict = 'APPROVED'
    ORDER BY last_assessed DESC
    """
    return ws_query(sql)

def analyze_vendor_concentration(servers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze vendor concentration across multiple dimensions."""
    npm_vendors: Dict[str, Dict] = {}
    github_vendors: Dict[str, Dict] = {}
    domain_vendors: Dict[str, Dict] = {}
    
    for server in servers:
        url = server.get('url', '')
        name = server.get('name', '')
        server_id = server.get('server_id', '')
        
        vendor_id, vendor_type = determine_vendor_type(url)
        if not vendor_id:
            continue
        
        if vendor_type in ('npm_author', 'npm_scope'):
            if vendor_id not in npm_vendors:
                npm_vendors[vendor_id] = {"count": 0, "type": vendor_type, "servers": []}
            npm_vendors[vendor_id]["count"] += 1
            npm_vendors[vendor_id]["servers"].append({"id": server_id, "name": name, "url": url})
        
        elif vendor_type == 'github_org':
            if vendor_id not in github_vendors:
                github_vendors[vendor_id] = {"count": 0, "type": "github_org", "servers": []}
            github_vendors[vendor_id]["count"] += 1
            github_vendors[vendor_id]["servers"].append({"id": server_id, "name": name, "url": url})
        
        elif vendor_type == 'domain':
            if vendor_id not in domain_vendors:
                domain_vendors[vendor_id] = {"count": 0, "type": "domain", "servers": []}
            domain_vendors[vendor_id]["count"] += 1
            domain_vendors[vendor_id]["servers"].append({"id": server_id, "name": name, "url": url})
    
    total_approved = len(servers)
    
    def calculate_risks(vendors: Dict, source_name: str) -> List[Dict[str, Any]]:
        """Calculate concentration risk for a vendor group."""
        risks = []
        if total_approved == 0:
            return risks
        
        sorted_vendors = sorted(vendors.items(), key=lambda x: x[1]["count"], reverse=True)
        
        for vendor_id, data in sorted_vendors:
            percentage = (data["count"] / total_approved) * 100
            risk_score = 0
            risk_level = "INFO"
            
            if data["count"] > 5:
                risk_score += 15
                risk_level = "WARNING"
            
            if percentage > 40:
                risk_score += 25
                risk_level = "HIGH"
            elif percentage > 30:
                risk_score += 10
                risk_level = "WARNING"
            
            risks.append({
                "vendor": vendor_id,
                "vendor_type": data["type"],
                "source": source_name,
                "server_count": data["count"],
                "percentage": round(percentage, 2),
                "risk_score": risk_score,
                "risk_level": risk_level,
                "servers": data["servers"][:10]
            })
        
        return risks
    
    all_risks = []
    all_risks.extend(calculate_risks(npm_vendors, "npm"))
    all_risks.extend(calculate_risks(github_vendors, "github"))
    all_risks.extend(calculate_risks(domain_vendors, "domain"))
    
    return {
        "total_approved": total_approved,
        "npm_vendor_count": len(npm_vendors),
        "github_vendor_count": len(github_vendors),
        "domain_vendor_count": len(domain_vendors),
        "total_vendors": len(npm_vendors) + len(github_vendors) + len(domain_vendors),
        "risks": all_risks,
        "top_npm_vendor": max(npm_vendors.items(), key=lambda x: x[1]["count"])[0] if npm_vendors else None,
        "top_github_vendor": max(github_vendors.items(), key=lambda x: x[1]["count"])[0] if github_vendors else None,
        "top_domain_vendor": max(domain_vendors.items(), key=lambda x: x[1]["count"])[0] if domain_vendors else None,
    }

def calculate_single_point_of_failure_risk(analysis: Dict[str, Any]) -> int:
    """Calculate risk for single-vendor dependency (vendor_count=1 AND approval_count>3)."""
    risk_score = 0
    total_vendors = analysis["total_vendors"]
    total_approved = analysis["total_approved"]
    
    if total_vendors == 1 and total_approved > 3:
        risk_score += 20
    
    for risk in analysis["risks"]:
        if risk["percentage"] > 70:
            risk_score += 15
        elif risk["percentage"] > 50:
            risk_score += 10
    
    return risk_score

def write_vendor_report(analysis: Dict[str, Any], risk_score: int) -> bool:
    """Write VENDOR_REPORT.md with concentration table."""
    try:
        report_path = os.path.join(os.path.dirname(__file__), "VENDOR_REPORT.md")
        
        with open(report_path, "w") as f:
            f.write("# Vendor Concentration Report\n\n")
            f.write(f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n")
            f.write(f"**Overall Risk Score:** {risk_score}\n\n")
            
            f.write("## Summary\n\n")
            f.write(f"- Total APPROVED Servers: {analysis['total_approved']}\n")
            f.write(f"- NPM Vendors: {analysis['npm_vendor_count']}\n")
            f.write(f"- GitHub Orgs: {analysis['github_vendor_count']}\n")
            f.write(f"- Domain Vendors: {analysis['domain_vendor_count']}\n")
            f.write(f"- **Total Unique Vendors:** {analysis['total_vendors']}\n\n")
            
            if analysis["top_npm_vendor"]:
                f.write(f"- Top NPM Vendor: `{analysis['top_npm_vendor']}`\n")
            if analysis["top_github_vendor"]:
                f.write(f"- Top GitHub Org: `{analysis['top_github_vendor']}`\n")
            if analysis["top_domain_vendor"]:
                f.write(f"- Top Domain: `{analysis['top_domain_vendor']}`\n")
            
            f.write("\n## Concentration Table\n\n")
            f.write("| Vendor | Type | Source | Servers | Percentage | Risk Level | Risk Score |\n")
            f.write("|--------|------|--------|---------|------------|------------|------------|\n")
            
            sorted_risks = sorted(analysis["risks"], key=lambda x: x["risk_score"], reverse=True)
            for risk in sorted_risks:
                badge = ""
                if risk["risk_level"] == "HIGH":
                    badge = " 🚨"
                elif risk["risk_level"] == "WARNING":
                    badge = " ⚠️"
                
                f.write(f"| `{risk['vendor']}` | {risk['vendor_type']} | {risk['source']} | "
                        f"{risk['server_count']} | {risk['percentage']}% | "
                        f"{risk['risk_level']}{badge} | {risk['risk_score']} |\n")
            
            f.write("\n## Risk Thresholds\n\n")
            f.write("- **concentration_warning:** Single vendor has >5 APPROVED servers\n")
            f.write("- **concentration_high:** Top vendor provides >40% of all approved servers\n")
            f.write("- **single_point_of_failure:** vendor_count=1 AND approval_count>3 (+20 risk)\n")
            
            f.write("\n## Recommendations\n\n")
            if risk_score >= 40:
                f.write("🔴 **CRITICAL:** Immediate diversification required. Identify backup vendors.\n\n")
            elif risk_score >= 25:
                f.write("🟡 **HIGH:** Reduce concentration. Favor multi-vendor solutions.\n\n")
            elif risk_score >= 10:
                f.write("🟢 **MODERATE:** Monitor and plan for vendor diversification.\n\n")
            else:
                f.write("✅ **LOW:** Healthy vendor distribution.\n\n")
            
            high_risks = [r for r in sorted_risks if r["risk_level"] in ("HIGH", "WARNING")]
            if high_risks:
                f.write("### High-Risk Vendors\n\n")
                for risk in high_risks:
                    f.write(f"- **{risk['vendor']}** ({risk['source']}): {risk['server_count']} servers, "
                            f"{risk['percentage']}% of total\n")
        
        log.info(f"Wrote VENDOR_REPORT.md")
        return True
    except Exception as e:
        log.error(f"Failed to write vendor report: {e}")
        return False

def write_alerts(analysis: Dict[str, Any], risk_score: int) -> None:
    """Write concentration alerts to mesh_events."""
    alerts = []
    
    for risk in analysis["risks"]:
        if risk["risk_score"] > 0:
            severity = "WARNING" if risk["percentage"] > 30 else "INFO"
            alert = {
                "event_type": "vendor_concentration_alert",
                "timestamp": datetime.utcnow().isoformat(),
                "severity": severity,
                "vendor": risk["vendor"],
                "vendor_type": risk["vendor_type"],
                "source": risk["source"],
                "server_count": risk["server_count"],
                "percentage": risk["percentage"],
                "risk_level": risk["risk_level"],
                "risk_score": risk["risk_score"],
                "message": f"Vendor {risk['vendor']} has {risk['server_count']} approved servers "
                          f"({risk['percentage']}% of total)"
            }
            alerts.append(alert)
    
    if alerts:
        ws_write("mesh_events", alerts)
        log.info(f"Wrote {len(alerts)} concentration alerts")

def ensure_mesh_events_table() -> None:
    """Ensure mesh_events table exists."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS mesh_events (
        id BIGINT PRIMARY KEY,
        event_type VARCHAR,
        timestamp TIMESTAMPTZ DEFAULT now(),
        severity VARCHAR,
        payload JSON,
        processed BOOLEAN DEFAULT FALSE
    )
    """
    try:
        requests.post(EXECUTE_URL, json={"sql": create_sql}, timeout=10)
    except Exception as e:
        log.warning(f"Table creation check failed: {e}")

def run():
    """Main daemon loop."""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    poll_interval = 43200
    
    if not check_single_instance(pid_file):
        log.error("Failed to acquire lock")
        return
    
    try:
        ensure_mesh_events_table()
        log.info(f"{SERVICE_NAME} starting...")
        
        send_heartbeat()
        
        while True:
            try:
                log.info("Analyzing vendor concentration...")
                
                servers = get_approved_servers()
                log.info(f"Found {len(servers)} APPROVED servers")
                
                if servers:
                    analysis = analyze_vendor_concentration(servers)
                    risk_score = calculate_single_point_of_failure_risk(analysis)
                    
                    log.info(f"Analysis complete: {analysis['total_vendors']} vendors, "
                            f"risk_score={risk_score}")
                    
                    write_vendor_report(analysis, risk_score)
                    write_alerts(analysis, risk_score)
                else:
                    log.info("No APPROVED servers to analyze")
                
                send_heartbeat()
                log.info(f"Sleeping {poll_interval}s until next scan...")
                time.sleep(poll_interval)
                
            except Exception as e:
                log.error(f"Cycle error: {e}")
                time.sleep(60)
                
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        remove_pid_file(pid_file)

if __name__ == "__main__":
    run()