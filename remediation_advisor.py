import requests
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime

log = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8773/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"

REMEDIATION_RULES = {
    "tool_description_safety": {
        "threshold": 40,
        "severity": "HIGH",
        "steps": [
            {
                "priority": 1,
                "action": "Conduct manual review of tool descriptions for injection patterns",
                "effort_hours": 2,
                "responsible_team": "security",
                "details": "Check for command injection, prompt injection, and malicious parameter patterns in all tool descriptions."
            },
            {
                "priority": 2,
                "action": "Implement automated description scanning using text_patterns module",
                "effort_hours": 4,
                "responsible_team": "developer",
                "details": "Integrate text_patterns.py scanning into the assessment pipeline to catch future injection attempts."
            },
            {
                "priority": 3,
                "action": "Add input sanitization guards around all tool parameter processing",
                "effort_hours": 8,
                "responsible_team": "developer",
                "details": "Implement parameterized queries and input validation for all tool parameters."
            }
        ]
    },
    "supply_chain_score": {
        "threshold": 30,
        "severity": "CRITICAL",
        "steps": [
            {
                "priority": 1,
                "action": "Verify package provenance and check npm author identity",
                "effort_hours": 1,
                "responsible_team": "security",
                "details": "Cross-reference package metadata with known-good registries and verify author fingerprints."
            },
            {
                "priority": 2,
                "action": "Review npm package download statistics and publication history",
                "effort_hours": 1,
                "responsible_team": "security",
                "details": "Check for suspicious patterns like sudden popularity spikes or recently created packages."
            },
            {
                "priority": 3,
                "action": "Enable dependency pinning and checksum verification in CI/CD pipeline",
                "effort_hours": 4,
                "responsible_team": "platform",
                "details": "Add package-lock.json enforcement and SHA256 verification steps."
            },
            {
                "priority": 4,
                "action": "Implement npm audit integration in build process",
                "effort_hours": 2,
                "responsible_team": "platform",
                "details": "Add automated security vulnerability scanning for all dependencies."
            }
        ]
    },
    "rug_pull": {
        "severity": "CRITICAL",
        "steps": [
            {
                "priority": 1,
                "action": "Immediately revoke all active sessions using this MCP",
                "effort_hours": 0.5,
                "responsible_team": "security",
                "details": "Terminate all authenticated sessions and invalidate active API keys for this MCP server."
            },
            {
                "priority": 2,
                "action": "Block the MCP server in all environments (dev, staging, production)",
                "effort_hours": 0.5,
                "responsible_team": "platform",
                "details": "Update firewall rules, API gateways, and service meshes to block traffic to this server."
            },
            {
                "priority": 3,
                "action": "Audit logs for any data exfiltration attempts in past 30 days",
                "effort_hours": 4,
                "responsible_team": "security",
                "details": "Review access logs, network traffic, and database queries from this MCP server."
            },
            {
                "priority": 4,
                "action": "Rotate all credentials and secrets that were accessible to this MCP",
                "effort_hours": 2,
                "responsible_team": "security",
                "details": "Regenerate API keys, passwords, tokens, and certificates that were in scope."
            },
            {
                "priority": 5,
                "action": "Notify affected teams and stakeholders of potential compromise",
                "effort_hours": 1,
                "responsible_team": "security",
                "details": "Send security incident notification to all teams that used this MCP server."
            }
        ]
    },
    "known_threat": {
        "severity": "CRITICAL",
        "steps": [
            {
                "priority": 1,
                "action": "Immediately isolate and disable the MCP server",
                "effort_hours": 0.5,
                "responsible_team": "security",
                "details": "Remove from service mesh, block in WAF, and disable all integrations."
            },
            {
                "priority": 2,
                "action": "Conduct full incident response according to IR plan",
                "effort_hours": 8,
                "responsible_team": "security",
                "details": "Follow established incident response procedures including forensics and containment."
            },
            {
                "priority": 3,
                "action": "Perform threat hunting across all connected systems",
                "effort_hours": 16,
                "responsible_team": "security",
                "details": "Check for lateral movement, persistence mechanisms, and data exfiltration."
            }
        ]
    },
    "low_trust_score": {
        "threshold": 0.4,
        "severity": "HIGH",
        "steps": [
            {
                "priority": 1,
                "action": "Review all tool definitions and function signatures for security issues",
                "effort_hours": 3,
                "responsible_team": "security",
                "details": "Manual security review of all exposed tools and their parameters."
            },
            {
                "priority": 2,
                "action": "Verify authentication and authorization implementation",
                "effort_hours": 2,
                "responsible_team": "developer",
                "details": "Ensure proper auth flows, token validation, and permission checks are in place."
            },
            {
                "priority": 3,
                "action": "Implement rate limiting and request throttling",
                "effort_hours": 2,
                "responsible_team": "platform",
                "details": "Add API gateway rate limits to prevent abuse and DoS attempts."
            },
            {
                "priority": 4,
                "action": "Enable comprehensive audit logging for all MCP interactions",
                "effort_hours": 4,
                "responsible_team": "platform",
                "details": "Log all requests, responses, and state changes for security monitoring."
            }
        ]
    },
    "insufficient_data": {
        "severity": "MEDIUM",
        "steps": [
            {
                "priority": 1,
                "action": "Complete full security assessment of the MCP server",
                "effort_hours": 8,
                "responsible_team": "security",
                "details": "Run mcp_scanner, threat_intel_ingestor, and risk_ranker to gather complete signal data."
            },
            {
                "priority": 2,
                "action": "Verify server identity and source repository authenticity",
                "effort_hours": 2,
                "responsible_team": "security",
                "details": "Check git history, verify signatures, and confirm maintainer identity."
            },
            {
                "priority": 3,
                "action": "Document all dependencies and their versions",
                "effort_hours": 1,
                "responsible_team": "developer",
                "details": "Create Software Bill of Materials (SBOM) for this MCP server."
            }
        ]
    },
    "authentication_missing": {
        "severity": "HIGH",
        "steps": [
            {
                "priority": 1,
                "action": "Implement authentication layer before deploying to any environment",
                "effort_hours": 8,
                "responsible_team": "developer",
                "details": "Add OAuth2, API key, or JWT authentication to all MCP endpoints."
            },
            {
                "priority": 2,
                "action": "Enable mTLS for all internal service-to-service communication",
                "effort_hours": 4,
                "responsible_team": "platform",
                "details": "Configure mutual TLS certificates for secure service mesh integration."
            },
            {
                "priority": 3,
                "action": "Add authorization framework with role-based access control",
                "effort_hours": 8,
                "responsible_team": "developer",
                "details": "Implement RBAC to control which users can invoke which tools."
            }
        ]
    },
    "high_confidence_negative": {
        "threshold": 0.3,
        "severity": "CRITICAL",
        "steps": [
            {
                "priority": 1,
                "action": "Quarantine MCP server immediately pending investigation",
                "effort_hours": 0.5,
                "responsible_team": "security",
                "details": "Move to isolated environment with monitoring for further analysis."
            },
            {
                "priority": 2,
                "action": "Conduct emergency security review",
                "effort_hours": 4,
                "responsible_team": "security",
                "details": "Full manual security assessment to determine nature and scope of threat."
            },
            {
                "priority": 3,
                "action": "Review all systems that have interacted with this MCP",
                "effort_hours": 8,
                "responsible_team": "security",
                "details": "Check for compromised data, unauthorized access, or credential theft."
            }
        ]
    },
    "weak_encryption": {
        "severity": "HIGH",
        "steps": [
            {
                "priority": 1,
                "action": "Update to TLS 1.3 for all connections",
                "effort_hours": 2,
                "responsible_team": "platform",
                "details": "Configure servers and clients to use TLS 1.3 with secure cipher suites."
            },
            {
                "priority": 2,
                "action": "Replace weak or deprecated cryptographic algorithms",
                "effort_hours": 4,
                "responsible_team": "developer",
                "details": "Remove MD5, SHA1, DES, and other weak algorithms from all cryptographic operations."
            },
            {
                "priority": 3,
                "action": "Implement certificate pinning for internal services",
                "effort_hours": 2,
                "responsible_team": "platform",
                "details": "Add certificate pinning to prevent MITM attacks."
            }
        ]
    },
    "data_leak_risk": {
        "severity": "HIGH",
        "steps": [
            {
                "priority": 1,
                "action": "Audit all data access patterns and query scopes",
                "effort_hours": 4,
                "responsible_team": "security",
                "details": "Review what data this MCP can access and ensure principle of least privilege."
            },
            {
                "priority": 2,
                "action": "Implement data masking for sensitive fields",
                "effort_hours": 4,
                "responsible_team": "developer",
                "details": "Add redaction and masking for PII, credentials, and sensitive business data."
            },
            {
                "priority": 3,
                "action": "Add data egress monitoring and alerting",
                "effort_hours": 4,
                "responsible_team": "platform",
                "details": "Configure DLP rules and alerts for unusual data access patterns."
            }
        ]
    }
}

DEFAULT_STEPS = [
    {
        "priority": 1,
        "action": "Document the security concern and add to remediation backlog",
        "effort_hours": 0.5,
        "responsible_team": "security",
        "details": "Create ticket with full findings and attach relevant evidence."
    },
    {
        "priority": 2,
        "action": "Schedule security review within next sprint",
        "effort_hours": 0.5,
        "responsible_team": "security",
        "details": "Add to security backlog with appropriate priority and acceptance criteria."
    }
]


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a query against the query service."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"Query failed: {sql} | Error: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write data to the write service using 'rows' field."""
    try:
        payload = {"table": table, "rows": rows}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed: {table} | Error: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute SQL via execute service."""
    try:
        payload = {"sql": sql}
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Execute failed: {sql} | Error: {e}")
        return False


def ensure_remediation_table() -> bool:
    """Create remediation tracking table if not exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS remediation_tracking (
        id BIGINT PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        finding_key VARCHAR NOT NULL,
        priority INTEGER,
        action TEXT,
        effort_hours REAL,
        responsible_team VARCHAR,
        status VARCHAR DEFAULT 'pending',
        assigned_at TIMESTAMPTZ DEFAULT now(),
        completed_at TIMESTAMPTZ,
        notes TEXT
    )
    """
    return ws_execute(sql)


def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    """Fetch server verdict and reasoning from registry."""
    sql = """
    SELECT server_id, name, verdict, verdict_reasoning, trust_score, 
           confidence, last_assessed, registry_source
    FROM mcp_server_registry 
    WHERE server_id = ?
    """
    rows = ws_query(sql, {"server_id": server_id})
    return rows[0] if rows else None


def get_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    """Fetch all signal scores for a server."""
    sql = """
    SELECT signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY scored_at DESC
    """
    return ws_query(sql, {"server_id": server_id})


def get_threat_associations(server_id: str) -> List[Dict[str, Any]]:
    """Fetch threat associations for a server."""
    sql = """
    SELECT threat_type, evidence, severity, reported_at
    FROM mcp_threat_associations
    WHERE server_id = ?
    ORDER BY reported_at DESC
    """
    return ws_query(sql, {"server_id": server_id})


def get_definition_hash_history(server_id: str) -> List[Dict[str, Any]]:
    """Fetch definition history for hash change detection."""
    sql = """
    SELECT snapshot_hash, captured_at
    FROM mcp_definition_history
    WHERE server_id = ?
    ORDER BY captured_at DESC
    LIMIT 10
    """
    return ws_query(sql, {"server_id": server_id})


def check_for_hash_drift(server_id: str) -> bool:
    """Check if definition has changed significantly (potential compromise)."""
    history = get_definition_hash_history(server_id)
    if len(history) < 2:
        return False
    hashes = [h.get("snapshot_hash") for h in history if h.get("snapshot_hash")]
    return len(set(hashes)) > 1


def determine_severity_from_verdict(verdict: str, confidence: float) -> str:
    """Map verdict to severity level."""
    critical_verdicts = ["KNOWN_THREAT", "HIGH_RISK_ISOLATED"]
    high_verdicts = ["CAUTION_LIMITED"]
    medium_verdicts = ["INSUFFICIENT"]
    
    if verdict in critical_verdicts:
        return "CRITICAL"
    elif verdict in high_verdicts:
        return "HIGH"
    elif verdict in medium_verdicts:
        return "MEDIUM"
    return "LOW"


def map_finding_to_remediation(finding_key: str, score: Optional[float] = None, 
                               verdict: Optional[str] = None, 
                               confidence: Optional[float] = None) -> List[Dict[str, Any]]:
    """Map a finding to remediation steps based on rules."""
    steps = []
    
    if finding_key in REMEDIATION_RULES:
        rule = REMEDIATION_RULES[finding_key]
        if "threshold" in rule:
            if score is not None and score < rule["threshold"]:
                steps = rule["steps"].copy()
        else:
            steps = rule["steps"].copy()
    
    if finding_key == "low_trust_score" and verdict:
        threshold = REMEDIATION_RULES.get("low_trust_score", {}).get("threshold", 0.4)
        if score is not None and score < threshold:
            steps = REMEDIATION_RULES["low_trust_score"]["steps"].copy()
    
    if finding_key == "high_confidence_negative" and confidence and score:
        threshold = REMEDIATION_RULES.get("high_confidence_negative", {}).get("threshold", 0.3)
        if confidence > 0.8 and score < threshold:
            steps = REMEDIATION_RULES["high_confidence_negative"]["steps"].copy()
    
    if finding_key == "known_threat" and verdict == "KNOWN_THREAT":
        steps = REMEDIATION_RULES["known_threat"]["steps"].copy()
    
    if finding_key == "insufficient_data" and verdict == "INSUFFICIENT":
        steps = REMEDIATION_RULES["insufficient_data"]["steps"].copy()
    
    return steps


def get_remediation_steps(server_id: str) -> List[Dict[str, Any]]:
    """
    Get ordered list of remediation steps for a server.
    Returns steps sorted by priority with metadata.
    """
    ensure_remediation_table()
    
    all_steps = []
    seen_actions = set()
    
    verdict_data = get_server_verdict(server_id)
    if not verdict_data:
        log.warning(f"No verdict found for server_id: {server_id}")
        return DEFAULT_STEPS
    
    verdict = verdict_data.get("verdict", "")
    trust_score = verdict_data.get("trust_score", 0.0)
    confidence = verdict_data.get("confidence", 0.0)
    severity = determine_severity_from_verdict(verdict, confidence)
    
    if severity == "CRITICAL":
        if verdict == "KNOWN_THREAT":
            steps = map_finding_to_remediation("known_threat")
            all_steps.extend(steps)
    
    if severity in ["CRITICAL", "HIGH"]:
        steps = map_finding_to_remediation("low_trust_score", trust_score)
        all_steps.extend(steps)
    
    signals = get_signal_scores(server_id)
    signal_map = {s["signal_name"]: s["score"] for s in signals}
    
    for signal_name, score in signal_map.items():
        if signal_name == "tool_description_safety" and score < 40:
            steps = map_finding_to_remediation("tool_description_safety", score)
            all_steps.extend(steps)
        elif signal_name == "supply_chain_score" and score < 30:
            steps = map_finding_to_remediation("supply_chain_score", score)
            all_steps.extend(steps)
        elif signal_name == "encryption_strength" and score < 50:
            steps = map_finding_to_remediation("weak_encryption", score)
            all_steps.extend(steps)
        elif signal_name == "data_protection_score" and score < 40:
            steps = map_finding_to_remediation("data_leak_risk", score)
            all_steps.extend(steps)
        elif signal_name == "authentication_score" and score < 30:
            steps = map_finding_to_remediation("authentication_missing", score)
            all_steps.extend(steps)
    
    threats = get_threat_associations(server_id)
    for threat in threats:
        threat_type = threat.get("threat_type", "").lower()
        if "rug_pull" in threat_type:
            steps = map_finding_to_remediation("rug_pull")
            all_steps.extend(steps)
        elif "supply_chain" in threat_type:
            steps = map_finding_to_remediation("supply_chain_score", 0)
            all_steps.extend(steps)
        elif "malicious" in threat_type:
            steps = map_finding_to_remediation("known_threat")
            all_steps.extend(steps)
    
    if check_for_hash_drift(server_id):
        drift_steps = [
            {
                "priority": 1,
                "action": "URGENT: Definition hash changed - investigate for unauthorized modifications",
                "effort_hours": 4,
                "responsible_team": "security",
                "details": "Definition history shows unexpected changes. Review git history and deployment logs."
            }
        ]
        all_steps = drift_steps + all_steps
    
    if verdict == "INSUFFICIENT":
        steps = map_finding_to_remediation("insufficient_data")
        all_steps.extend(steps)
    
    unique_steps = []
    for step in all_steps:
        action_key = step["action"][:50]
        if action_key not in seen_actions:
            seen_actions.add(action_key)
            unique_steps.append(step)
    
    unique_steps.sort(key=lambda x: x["priority"])
    
    for i, step in enumerate(unique_steps):
        step["step_number"] = i + 1
    
    return unique_steps


def generate_remediation_report(server_id: str) -> str:
    """
    Generate a full markdown remediation plan for a server.
    """
    ensure_remediation_table()
    
    server_data = get_server_verdict(server_id)
    if not server_data:
        return f"# Remediation Report\n\n**Error:** Server `{server_id}` not found in registry.\n"
    
    steps = get_remediation_steps(server_id)
    signals = get_signal_scores(server_id)
    threats = get_threat_associations(server_id)
    
    server_name = server_data.get("name", server_id)
    verdict = server_data.get("verdict", "UNKNOWN")
    trust_score = server_data.get("trust_score", 0.0)
    confidence = server_data.get("confidence", 0.0)
    last_assessed = server_data.get("last_assessed", "Never")
    reasoning = server_data.get("verdict_reasoning", "No reasoning provided")
    registry_source = server_data.get("registry_source", "Unknown")
    
    severity = determine_severity_from_verdict(verdict, confidence)
    
    total_effort = sum(s.get("effort_hours", 0) for s in steps)
    by_team = {}
    for step in steps:
        team = step.get("responsible_team", "unknown")
        by_team[team] = by_team.get(team, 0) + step.get("effort_hours", 0)
    
    report = f"""# Remediation Report: {server_name}

## Executive Summary

| Field | Value |
|-------|-------|
| **Server ID** | `{server_id}` |
| **Registry Source** | {registry_source} |
| **Current Verdict** | {verdict} |
| **Trust Score** | {trust_score:.2f} |
| **Confidence** | {confidence:.2f} |
| **Risk Severity** | {severity} |
| **Last Assessed** | {last_assessed} |
| **Total Remediation Effort** | {total_effort} hours |

## Verdict Reasoning

{reasoning}

## Security Findings

### Signal Scores
"""
    
    if signals:
        report += "| Signal | Score | Status |\n|--------|-------|--------|\n"
        for sig in signals:
            score = sig.get("score", 0)
            status = "⚠️ Low" if score < 50 else "✅ Good" if score >= 70 else "⚡ Medium"
            report += f"| {sig.get('signal_name', 'unknown')} | {score:.1f} | {status} |\n"
    else:
        report += "*No signal scores available.*\n"
    
    report += "\n### Threat Associations\n"
    if threats:
        report += "| Threat Type | Severity | Evidence | Reported |\n|-------------|----------|----------|----------|\n"
        for t in threats:
            report += f"| {t.get('threat_type', 'unknown')} | {t.get('severity', 'N/A')} | {t.get('evidence', 'N/A')[:50]}... | {t.get('reported_at', 'N/A')} |\n"
    else:
        report += "*No threat associations found.*\n"
    
    report += f"""
## Remediation Plan

### Overview

- **Total Steps:** {len(steps)}
- **Estimated Total Effort:** {total_effort} hours
- **Breakdown by Team:**
"""
    for team, hours in sorted(by_team.items()):
        report += f"  - **{team.capitalize()}:** {hours} hours\n"
    
    report += "\n---\n\n"
    
    if not steps:
        report += "**No specific remediation steps required.** Server meets security standards.\n"
    else:
        current_priority = None
        for step in steps:
            priority = step.get("priority", 99)
            if current_priority != priority:
                current_priority = priority
                report += f"\n### Priority {priority}\n\n"
            
            report += f"#### Step {step.get('step_number', '?')}: {step.get('action', 'Unknown action')}\n\n"
            report += f"- **Estimated Effort:** {step.get('effort_hours', 0)} hours\n"
            report += f"- **Responsible Team:** {step.get('responsible_team', 'unknown').capitalize()}\n"
            report += f"- **Details:** {step.get('details', 'No details provided')}\n\n"
    
    report += """## Action Items

Please track the following in your security management system:

1. Assign each remediation step to the responsible team
2. Set deadlines based on priority levels
3. Update status as items are completed
4. Document any blockers or escalations

## Next Steps

1. **Immediate (24-48 hours):** Address Priority 1 items
2. **This Week:** Complete Priority 2-3 items
3. **This Sprint:** Address all remaining items
4. **Verification:** Re-run assessment after remediation to verify fixes

---

*Report generated: {generated_at}*
*ZO-SENTINEL Remediation Advisor*
""".format(generated_at=datetime.utcnow().isoformat())
    
    return report


def record_remediation_steps(server_id: str, steps: List[Dict[str, Any]]) -> bool:
    """Record remediation steps to the tracking table."""
    ensure_remediation_table()
    
    success = True
    for step in steps:
        row = {
            "server_id": server_id,
            "finding_key": step.get("action", "unknown")[:100],
            "priority": step.get("priority", 99),
            "action": step.get("action", ""),
            "effort_hours": step.get("effort_hours", 0),
            "responsible_team": step.get("responsible_team", "security"),
            "status": "pending"
        }
        if not ws_write("remediation_tracking", row):
            success = False
    
    return success


def update_remediation_status(server_id: str, action: str, status: str, notes: str = "") -> bool:
    """Update the status of a remediation step."""
    sql = f"""
    UPDATE remediation_tracking 
    SET status = ?, completed_at = CASE WHEN ? = 'completed' THEN now() ELSE completed_at END,
        notes = COALESCE(notes || E'\\n' || ?, notes)
    WHERE server_id = ? AND action = ?
    """
    return ws_execute(sql)


def get_remediation_summary(server_id: str) -> Dict[str, Any]:
    """Get a summary of remediation progress for a server."""
    sql = """
    SELECT status, COUNT(*) as count, SUM(effort_hours) as total_effort
    FROM remediation_tracking
    WHERE server_id = ?
    GROUP BY status
    """
    rows = ws_query(sql, {"server_id": server_id})
    
    summary = {
        "server_id": server_id,
        "pending": {"count": 0, "effort": 0},
        "in_progress": {"count": 0, "effort": 0},
        "completed": {"count": 0, "effort": 0},
        "blocked": {"count": 0, "effort": 0}
    }
    
    for row in rows:
        status = row.get("status", "pending")
        if status in summary:
            summary[status] = {
                "count": row.get("count", 0),
                "effort": row.get("total_effort", 0)
            }
    
    return summary


def main():
    """CLI entry point for testing."""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python remediation_advisor.py <server_id>")
        print("       python remediation_advisor.py <server_id> --report")
        sys.exit(1)
    
    server_id = sys.argv[1]
    generate_report = "--report" in sys.argv
    
    steps = get_remediation_steps(server_id)
    print(f"\nRemediation steps for {server_id}:\n")
    
    for step in steps:
        print(f"[P{step.get('priority', '?')}] {step.get('action', 'Unknown')}")
        print(f"  Team: {step.get('responsible_team', 'unknown')} | Effort: {step.get('effort_hours', 0)}h")
        print(f"  Details: {step.get('details', 'N/A')}")
        print()
    
    if generate_report:
        report = generate_remediation_report(server_id)
        print("\n" + "="*80)
        print(report)


if __name__ == "__main__":
    main()