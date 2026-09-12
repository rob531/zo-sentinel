import argparse
import sys
import json
import subprocess
import os
from typing import Optional, Dict, Any, List

# ANSI color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
MAGENTA = '\033[95m'
CYAN = '\033[96m'
WHITE = '\033[97m'
BOLD = '\033[1m'
DIM = '\033[2m'
RESET = '\033[0m'

# Verdict colors
VERDICT_COLORS = {
    "TRUSTED_GENERAL": GREEN,
    "TRUSTED_RESEARCH": BLUE,
    "ENTERPRISE_CONTROLLED": YELLOW,
    "CAUTION_LIMITED": MAGENTA,
    "HIGH_RISK_ISOLATED": RED,
    "KNOWN_THREAT": RED,
    "INSUFFICIENT": DIM,
}

RISK_TIER_COLORS = {
    "TIER1_CRITICAL": RED,
    "TIER2_HIGH": MAGENTA,
    "TIER3_MEDIUM": YELLOW,
    "TIER4_LOW": GREEN,
    "TIER5_MINIMAL": DIM,
}

SERVICE_NAME = "sentinel_cli"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8773/query"
EXECUTE_URL = "http://127.0.0.1:8773/execute"
DEFAULT_BASE_URL = "http://localhost:8787"


def ws_query(query: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute a query against the query service."""
    payload = {"query": query}
    if params:
        payload["params"] = params
    response = subprocess.run(
        ["curl", "-s", "-X", "POST", QUERY_URL, "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=30
    )
    try:
        return json.loads(response.stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse response", "raw": response.stdout}


def ws_write(table: str, rows: Dict[str, Any]) -> Dict[str, Any]:
    """Write data to the write service."""
    payload = {"table": table, "rows": rows}
    response = subprocess.run(
        ["curl", "-s", "-X", "POST", WRITE_SERVICE_URL, "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
        capture_output=True, text=True, timeout=30
    )
    try:
        return json.loads(response.stdout)
    except json.JSONDecodeError:
        return {"error": "Failed to parse response", "raw": response.stdout}


def color_verdict(verdict: str) -> str:
    """Colorize a verdict string."""
    color = VERDICT_COLORS.get(verdict, WHITE)
    return f"{color}{verdict}{RESET}"


def color_risk_tier(tier: str) -> str:
    """Colorize a risk tier string."""
    color = RISK_TIER_COLORS.get(tier, WHITE)
    return f"{color}{tier}{RESET}"


def color_score(score: float) -> str:
    """Colorize a trust score."""
    if score >= 80:
        return f"{GREEN}{score:.1f}{RESET}"
    elif score >= 60:
        return f"{YELLOW}{score:.1f}{RESET}"
    elif score >= 40:
        return f"{MAGENTA}{score:.1f}{RESET}"
    else:
        return f"{RED}{score:.1f}{RESET}"


def print_banner():
    """Print the ZO-SENTINEL banner."""
    banner = f"""
{CYAN}{BOLD}
  ██████╗ ██████╗ ███████╗██╗██████╗ ██╗ █████╗ 
  ██╔══██╗██╔══██╗██╔════╝██║██╔══██╗██║██╔══██╗
  ██████╔╝██████╔╝███████╗██║██║  ██║██║███████║
  ██╔══██╗██╔══██╗╚════██║██║██║  ██║██║██╔══██║
  ██████╔╝██████╔╝███████║██║██████╔╝██║██║  ██║
  ╚═════╝ ╚═════╝ ╚══════╝╚═╝╚═════╝ ╚═╝╚═╝  ╚═╝
{RESET} {MAGENTA}Safety Intelligence for Enterprise InfoSec{RESET}
"""
    print(banner)


def cmd_assess(args) -> int:
    """Run a full assessment on an MCP server."""
    server_name = args.server
    
    print(f"\n{BOLD}{CYAN}🔍 Running assessment on: {server_name}{RESET}\n")
    
    # Query for server data
    result = ws_query("""
        SELECT r.server_id, r.name, r.description,
               r.url AS server_url, r.verdict, r.trust_score, r.risk_tier,
               (SELECT a.status FROM mcp_attestations a
                 WHERE a.server_id = r.server_id
                 ORDER BY a.generated_at DESC LIMIT 1) AS attestation_status,
               r.last_assessed, r.first_seen AS registered_at
        FROM mcp_server_registry r
        WHERE r.name LIKE ? OR r.url LIKE ? OR r.server_id = ?
        LIMIT 1
    """, {"p1": f"%{server_name}%", "p2": f"%{server_name}%", "p3": server_name})
    
    servers = result.get("data", {}).get("results", [])
    
    if not servers:
        print(f"{YELLOW}⚠ Server not found: {server_name}{RESET}")
        print(f"{DIM}You may need to register this server first with: submit <url> --name <name>{RESET}\n")
        return 1
    
    server = servers[0]
    
    # Display assessment results
    print(f"{BOLD}{'─' * 60}{RESET}")
    print(f"{BOLD}📋 Assessment Results for: {server.get('name', 'Unknown')}{RESET}")
    print(f"{'─' * 60}")
    
    print(f"\n  {DIM}Server ID:{RESET}   {server.get('server_id', 'N/A')}")
    print(f"  {DIM}URL:{RESET}         {server.get('server_url', 'N/A')}")
    print(f"  {DIM}Description:{RESET} {server.get('description', 'N/A')}")
    
    print(f"\n  {DIM}Verdict:{RESET}     {color_verdict(server.get('verdict', 'INSUFFICIENT'))}")
    print(f"  {DIM}Trust Score:{RESET} {color_score(server.get('trust_score', 0))}")
    print(f"  {DIM}Risk Tier:{RESET}   {color_risk_tier(server.get('risk_tier', 'TIER4_LOW'))}")
    
    attestation = server.get('attestation_status', 'none')
    if attestation == 'active':
        print(f"  {DIM}Attestation:{RESET} {GREEN}✓ Active{RESET}")
    elif attestation == 'expired':
        print(f"  {DIM}Attestation:{RESET} {YELLOW}⚠ Expired{RESET}")
    else:
        print(f"  {DIM}Attestation:{RESET} {DIM}None{RESET}")
    
    print(f"\n  {DIM}Last Assessed:{RESET} {server.get('last_assessed', 'Never')}")
    print(f"  {DIM}Registered:{RESET}    {server.get('registered_at', 'Unknown')}")
    print(f"{'─' * 60}")
    
    # Get signals for this server
    signals_result = ws_query("""
        SELECT signal_name AS signal_type,
               score       AS signal_value,
               scored_at   AS timestamp
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY scored_at DESC
        LIMIT 10
    """, {"p1": server.get('server_id')})
    
    signals = signals_result.get("data", {}).get("results", [])
    if signals:
        print(f"\n  {BOLD}Top Signals:{RESET}")
        for sig in signals[:5]:
            sig_type = sig.get('signal_type', 'unknown')
            sig_val = sig.get('signal_value', 0)
            weight = sig.get('weight', 1)
            print(f"    • {sig_type}: {color_score(sig_val * weight)} (weight: {weight})")
    
    print()
    return 0


def cmd_search(args) -> int:
    """Search for MCP servers."""
    query = args.query
    
    print(f"\n{BOLD}{CYAN}🔎 Searching for: {query}{RESET}\n")
    
    result = ws_query("""
        SELECT server_id, name, url AS server_url, verdict, trust_score, risk_tier
        FROM mcp_server_registry
        WHERE name LIKE ? OR description LIKE ? OR url LIKE ?
        ORDER BY trust_score DESC
        LIMIT 20
    """, {"p1": f"%{query}%", "p2": f"%{query}%", "p3": f"%{query}%"})
    
    servers = result.get("data", {}).get("results", [])
    
    if not servers:
        print(f"{YELLOW}⚠ No servers found matching: {query}{RESET}\n")
        return 1
    
    print(f"{BOLD}{'Server ID':<40} {'Name':<25} {'Verdict':<20} {'Score':<8}{RESET}")
    print(f"{DIM}{'─' * 100}{RESET}")
    
    for server in servers:
        sid = server.get('server_id', '')[:38]
        name = server.get('name', 'Unknown')[:23]
        verdict = color_verdict(server.get('verdict', 'INSUFFICIENT'))
        score = color_score(server.get('trust_score', 0))
        
        print(f"{sid:<40} {name:<25} {verdict:<20} {score:<8}")
    
    print(f"\n{DIM}Found {len(servers)} server(s){RESET}\n")
    return 0


def cmd_submit(args) -> int:
    """Submit a new MCP server for assessment."""
    url = args.url
    name = args.name or url.split('/')[-1]
    description = args.description or f"MCP server at {url}"
    
    print(f"\n{BOLD}{CYAN}📤 Submitting server for assessment:{RESET}")
    print(f"  URL: {url}")
    print(f"  Name: {name}")
    print(f"  Description: {description}\n")
    
    # Generate server_id
    import hashlib
    server_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    
    result = ws_write("mcp_servers", {
        "server_id": server_id,
        "name": name,
        "server_url": url,
        "description": description,
        "verdict": "INSUFFICIENT",
        "trust_score": 0.0,
        "risk_tier": "TIER5_MINIMAL",
        "attestation_status": "none",
        "registered_at": "now()"
    })
    
    if result.get("success") or result.get("status") == "ok":
        print(f"{GREEN}✓ Server submitted successfully!{RESET}")
        print(f"{DIM}Server ID: {server_id}{RESET}")
        print(f"{DIM}Run assessment with: assess {name}{RESET}\n")
        return 0
    else:
        print(f"{RED}✗ Failed to submit server: {result.get('error', 'Unknown error')}{RESET}\n")
        return 1


def cmd_status(args) -> int:
    """Show pipeline health status."""
    print(f"\n{BOLD}{CYAN}🏥 Pipeline Health Status{RESET}\n")
    
    # Check services
    services = [
        ("write_service", WRITE_SERVICE_URL, "Write Service"),
        ("query_service", QUERY_URL, "Query Service"),
        ("execute_service", EXECUTE_URL, "Execute Service"),
    ]
    
    print(f"{BOLD}Service Availability:{RESET}")
    for svc_id, url, name in services:
        try:
            import urllib.request
            req = urllib.request.Request(url, method='GET')
            req.add_header('Content-Type', 'application/json')
            response = urllib.request.urlopen(req, timeout=5)
            status = f"{GREEN}✓ Running{RESET}"
        except Exception:
            status = f"{RED}✗ Unavailable{RESET}"
        print(f"  {name:<20} {status}")
    
    # Get pipeline stats
    result = ws_query("""
        SELECT 
            COUNT(*) as total_servers,
            SUM(CASE WHEN verdict = 'TRUSTED_GENERAL' OR verdict = 'TRUSTED_RESEARCH' THEN 1 ELSE 0 END) as trusted,
            SUM(CASE WHEN risk_tier = 'TIER1_CRITICAL' OR risk_tier = 'TIER2_HIGH' THEN 1 ELSE 0 END) as high_risk,
            SUM(CASE WHEN trust_score = 0 OR trust_score IS NULL THEN 1 ELSE 0 END) as unscored
        FROM mcp_server_registry
    """, {})
    
    stats = result.get("data", {}).get("results", [])
    if stats:
        s = stats[0]
        print(f"\n{BOLD}Registry Statistics:{RESET}")
        print(f"  Total Servers:     {BOLD}{s.get('total_servers', 0)}{RESET}")
        print(f"  Trusted:           {GREEN}{s.get('trusted', 0)}{RESET}")
        print(f"  High Risk:         {RED if s.get('high_risk', 0) > 0 else GREEN}{s.get('high_risk', 0)}{RESET}")
        print(f"  Unscored:          {YELLOW}{s.get('unscored', 0)}{RESET}")
    
    # Check last heartbeat from daemons
    result2 = ws_query("""
        SELECT service_name, last_heartbeat
        FROM service_health
        WHERE last_heartbeat > now() - INTERVAL '5 minutes'
        ORDER BY service_name
    """, {})
    
    daemons = result2.get("data", {}).get("results", [])
    if daemons:
        print(f"\n{BOLD}Active Daemons:{RESET}")
        for d in daemons:
            name = d.get('service_name', 'unknown')
            ts = d.get('last_heartbeat', 'unknown')
            print(f"  {GREEN}✓{RESET} {name:<30} {DIM}Last: {ts}{RESET}")
    else:
        print(f"\n{DIM}No active daemon heartbeats in last 5 minutes{RESET}")
    
    print()
    return 0


def cmd_risks(args) -> int:
    """Show high-risk servers."""
    tier = args.tier or "TIER1_CRITICAL"
    
    print(f"\n{BOLD}{RED}⚠ High Risk Servers (Tier: {tier}){RESET}\n")
    
    result = ws_query(f"""
        SELECT server_id, name, url AS server_url, verdict, trust_score,
               risk_tier, last_assessed
        FROM mcp_server_registry
        WHERE risk_tier IN ('TIER1_CRITICAL', 'TIER2_HIGH')
        ORDER BY trust_score ASC, last_assessed DESC
        LIMIT 50
    """, {})
    
    servers = result.get("data", {}).get("results", [])
    
    if not servers:
        print(f"{GREEN}✓ No high-risk servers found!{RESET}\n")
        return 0
    
    print(f"{BOLD}{'Name':<30} {'Risk Tier':<15} {'Score':<8} {'Verdict':<20} {'URL':<30}{RESET}")
    print(f"{DIM}{'─' * 110}{RESET}")
    
    for server in servers:
        name = server.get('name', 'Unknown')[:28]
        tier_str = color_risk_tier(server.get('risk_tier', 'TIER4_LOW'))
        score = color_score(server.get('trust_score', 0))
        verdict = color_verdict(server.get('verdict', 'INSUFFICIENT'))
        url = server.get('server_url', '')[:30]
        
        print(f"{name:<30} {tier_str:<15} {score:<8} {verdict:<20} {DIM}{url:<30}{RESET}")
    
    print(f"\n{DIM}Total high-risk: {len(servers)}{RESET}\n")
    return 0


def cmd_threats(args) -> int:
    """Show recent threat intelligence."""
    days = args.days or 7
    
    print(f"\n{BOLD}{RED}💀 Threat Intelligence (Last {days} days){RESET}\n")
    
    # `known_threats` is not a table -- known_threats.py is a MODULE of static
    # constants (KNOWN_MALICIOUS_PACKAGES, HIGH_RISK_PATTERNS) imported by
    # signal_analyser.py. A module name was read as a table name. The per-server
    # threat records live on the bus in mcp_threat_associations; the server's
    # own name and verdict come from the registry. Refs #4080.
    result = ws_query(f"""
        SELECT t.server_id,
               r.name,
               r.verdict,
               t.threat_type  AS threat_category,
               t.severity,
               t.reported_at  AS detected_at,
               t.evidence
        FROM mcp_threat_associations t
        LEFT JOIN mcp_server_registry r ON r.server_id = t.server_id
        WHERE t.reported_at > now() - INTERVAL '{days} days'
        ORDER BY t.reported_at DESC
        LIMIT 50
    """, {})
    
    threats = result.get("data", {}).get("results", [])
    
    if not threats:
        print(f"{GREEN}✓ No recent threats detected!{RESET}\n")
        return 0
    
    print(f"{BOLD}{'Server':<25} {'Verdict':<20} {'Category':<20} {'Severity':<15}{RESET}")
    print(f"{DIM}{'─' * 100}{RESET}")
    
    for threat in threats:
        name = threat.get('name', 'Unknown')[:23]
        verdict = color_verdict(threat.get('verdict', 'KNOWN_THREAT'))
        category = threat.get('threat_category', 'unknown')[:18]
        severity = str(threat.get('severity', 'unknown'))[:13]
        
        print(f"{RED}{name:<25}{RESET} {verdict:<20} {MAGENTA}{category:<20}{RESET} {YELLOW}{severity:<15}{RESET}")
    
    print(f"\n{DIM}Total threats: {len(threats)}{RESET}\n")
    return 0


def cmd_report(args) -> int:
    """Generate compliance report."""
    print(f"\n{BOLD}{CYAN}📊 Generating Compliance Report{RESET}\n")
    
    # Query summary stats
    result = ws_query("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN verdict IN ('TRUSTED_GENERAL', 'TRUSTED_RESEARCH') THEN 1 ELSE 0 END) as trusted,
            SUM(CASE WHEN verdict = 'ENTERPRISE_CONTROLLED' THEN 1 ELSE 0 END) as enterprise,
            SUM(CASE WHEN verdict = 'CAUTION_LIMITED' THEN 1 ELSE 0 END) as caution,
            SUM(CASE WHEN verdict IN ('HIGH_RISK_ISOLATED', 'KNOWN_THREAT') THEN 1 ELSE 0 END) as high_risk,
            SUM(CASE WHEN EXISTS (SELECT 1 FROM mcp_attestations a
                                   WHERE a.server_id = r.server_id
                                     AND a.status = 'active') THEN 1 ELSE 0 END) as attested,
            SUM(CASE WHEN EXISTS (SELECT 1 FROM mcp_attestations a
                                   WHERE a.server_id = r.server_id
                                     AND a.status = 'expired') THEN 1 ELSE 0 END) as expired
        FROM mcp_server_registry r
    """, {})
    
    stats = result.get("data", {}).get("results", [])
    
    if not stats:
        print(f"{RED}✗ Unable to generate report - no data available{RESET}\n")
        return 1
    
    s = stats[0]
    
    print(f"{BOLD}{'═' * 50}{RESET}")
    print(f"{BOLD}     ZO-SENTINEL COMPLIANCE REPORT{RESET}")
    print(f"{'═' * 50}\n")
    
    print(f"{BOLD}Server Registry Summary:{RESET}")
    print(f"  {'Total Registered:':<25} {BOLD}{s.get('total', 0)}{RESET}")
    print(f"  {'Trusted:':<25} {GREEN}{s.get('trusted', 0)}{RESET}")
    print(f"  {'Enterprise Controlled:':<25} {YELLOW}{s.get('enterprise', 0)}{RESET}")
    print(f"  {'Caution Limited:':<25} {MAGENTA}{s.get('caution', 0)}{RESET}")
    print(f"  {'High Risk/Threat:':<25} {RED}{s.get('high_risk', 0)}{RESET}")
    
    print(f"\n{BOLD}Attestation Status:{RESET}")
    print(f"  {'Active Attestations:':<25} {GREEN}{s.get('attested', 0)}{RESET}")
    print(f"  {'Expired Attestations:':<25} {RED}{s.get('expired', 0)}{RESET}")
    
    # Calculate compliance percentage
    if s.get('total', 0) > 0:
        compliance_pct = (s.get('attested', 0) / s.get('total', 0)) * 100
    else:
        compliance_pct = 0
    
    print(f"\n{BOLD}Compliance Rate:{RESET}")
    if compliance_pct >= 90:
        bar_color = GREEN
    elif compliance_pct >= 70:
        bar_color = YELLOW
    else:
        bar_color = RED
    
    bar_filled = int(compliance_pct / 5)
    bar = f"{bar_color}{'█' * bar_filled}{DIM}{'░' * (20 - bar_filled)}{RESET}"
    print(f"  [{bar}] {compliance_pct:.1f}%")
    
    # Risk distribution
    result2 = ws_query("""
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY risk_tier
        ORDER BY 
            CASE risk_tier
                WHEN 'TIER1_CRITICAL' THEN 1
                WHEN 'TIER2_HIGH' THEN 2
                WHEN 'TIER3_MEDIUM' THEN 3
                WHEN 'TIER4_LOW' THEN 4
                ELSE 5
            END
    """, {})
    
    tiers = result2.get("data", {}).get("results", [])
    if tiers:
        print(f"\n{BOLD}Risk Distribution:{RESET}")
        for t in tiers:
            tier_name = t.get('risk_tier', 'UNKNOWN')
            count = t.get('count', 0)
            tier_color = RISK_TIER_COLORS.get(tier_name, WHITE)
            print(f"  {tier_color}{tier_name:<15}{RESET} {count}")
    
    print(f"\n{BOLD}{'═' * 50}{RESET}")
    print(f"{DIM}Report generated at: now(){RESET}\n")
    
    return 0


def cmd_seed(args) -> int:
    """Run quick seed to populate test servers."""
    print(f"\n{BOLD}{CYAN}🌱 Running Quick Seed{RESET}\n")
    
    script_path = os.path.join(os.path.dirname(__file__), "quick_seed.py")
    
    if not os.path.exists(script_path):
        print(f"{RED}✗ quick_seed.py not found{RESET}\n")
        return 1
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=60
        )
        
        if result.returncode == 0:
            print(f"{GREEN}✓ Seed completed successfully{RESET}")
            if result.stdout:
                print(result.stdout)
            return 0
        else:
            print(f"{RED}✗ Seed failed:{RESET}")
            print(result.stderr)
            return 1
    except Exception as e:
        print(f"{RED}✗ Error running seed: {e}{RESET}\n")
        return 1


def cmd_validate(args) -> int:
    """Run configuration validator."""
    print(f"\n{BOLD}{CYAN}🔧 Running Configuration Validator{RESET}\n")
    
    script_path = os.path.join(os.path.dirname(__file__), "startup_checker.py")
    
    if not os.path.exists(script_path):
        print(f"{RED}✗ startup_checker.py not found{RESET}\n")
        return 1
    
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=60
        )
        
        if result.returncode == 0:
            print(f"{GREEN}✓ Configuration valid{RESET}")
            if result.stdout:
                print(result.stdout)
            return 0
        else:
            print(f"{RED}✗ Configuration validation failed:{RESET}")
            print(result.stderr)
            return 1
    except Exception as e:
        print(f"{RED}✗ Error running validator: {e}{RESET}\n")
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description=f"{CYAN}ZO-SENTINEL{RESET} - Safety Intelligence for Enterprise InfoSec",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  {BOLD}sentinel_cli.py assess myserver{RESET}       Run full assessment
  {BOLD}sentinel_cli.py search kubernetes{RESET}     Search for servers
  {BOLD}sentinel_cli.py submit http://... --name myserver{RESET}  Register new server
  {BOLD}sentinel_cli.py status{RESET}                Show pipeline health
  {BOLD}sentinel_cli.py risks --tier TIER1_CRITICAL{RESET}  List high-risk servers
  {BOLD}sentinel_cli.py threats --days 30{RESET}     Show recent threats
  {BOLD}sentinel_cli.py report{RESET}                Generate compliance report
  {BOLD}sentinel_cli.py seed{RESET}                  Run quick seed
  {BOLD}sentinel_cli.py validate{RESET}              Validate configuration

Use {BOLD}--json{RESET} flag for machine-readable output.
        """
    )
    
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # assess command
    assess_parser = subparsers.add_parser("assess", help="Run full assessment on an MCP server")
    assess_parser.add_argument("server", help="Server name, URL, or ID to assess")
    
    # search command
    search_parser = subparsers.add_parser("search", help="Search for MCP servers")
    search_parser.add_argument("query", help="Search query (name, URL, or description)")
    
    # submit command
    submit_parser = subparsers.add_parser("submit", help="Submit a new MCP server for assessment")
    submit_parser.add_argument("url", help="MCP server URL")
    submit_parser.add_argument("--name", help="Server name", default=None)
    submit_parser.add_argument("--description", help="Server description", default=None)
    
    # status command
    subparsers.add_parser("status", help="Show pipeline health status")
    
    # risks command
    risks_parser = subparsers.add_parser("risks", help="Show high-risk servers")
    risks_parser.add_argument("--tier", help="Risk tier filter (default: TIER1_CRITICAL)", default="TIER1_CRITICAL")
    
    # threats command
    threats_parser = subparsers.add_parser("threats", help="Show recent threat intelligence")
    threats_parser.add_argument("--days", type=int, help="Number of days to look back (default: 7)", default=7)
    
    # report command
    subparsers.add_parser("report", help="Generate compliance report")
    
    # seed command
    subparsers.add_parser("seed", help="Run quick seed to populate test servers")
    
    # validate command
    subparsers.add_parser("validate", help="Run configuration validator")
    
    args = parser.parse_args()
    
    if not args.command:
        print_banner()
        parser.print_help()
        return 0
    
    # Execute the appropriate command
    command_handlers = {
        "assess": cmd_assess,
        "search": cmd_search,
        "submit": cmd_submit,
        "status": cmd_status,
        "risks": cmd_risks,
        "threats": cmd_threats,
        "report": cmd_report,
        "seed": cmd_seed,
        "validate": cmd_validate,
    }
    
    handler = command_handlers.get(args.command)
    if handler:
        return handler(args)
    else:
        print(f"{RED}Unknown command: {args.command}{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())