#!/usr/bin/env python3
"""
tool_schema_deep_scanner.py -- ZO-SENTINEL deep tool schema scanner daemon.
Analyzes MCP server tool definitions for dangerous schema patterns.
Polls registry, fetches tool schemas, and detects security vulnerabilities.
"""
import os
import re
import json
import time
import hashlib
import logging
import requests
from typing import Dict, List, Any, Optional, Set
from datetime import datetime, timezone
from urllib.parse import urlparse

log = logging.getLogger(__name__)

SERVICE_NAME = "tool_schema_deep_scanner"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"

HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 21600
FETCH_TIMEOUT = 5

THREAT_TABLE = "mcp_threat_associations"
SIGNAL_TABLE = "mcp_signal_scores"
REGISTRY_TABLE = "mcp_server_registry"

IP_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
)

INTERNAL_HOSTNAME_PATTERNS = [
    r'\b(?:localhost|127\.0\.0\.1)\b',
    r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b',
    r'\b(?:172\.(?:1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3})\b',
    r'\b(?:192\.168\.\d{1,3}\.\d{1,3})\b',
    r'\b(?:internal|intranet|private)\b',
    r'\b(?:[\w-]+\.(?:local|internal|intranet|private))\b',
    r'\b(?:mongo|postgres|mysql|redis|elasticsearch)\b',
    r'\b(?:[a-zA-Z0-9-]+\.(?:corp|company|internal))\b',
]

URL_IN_DESCRIPTION_PATTERN = re.compile(
    r'https?://[^\s<>"{}|\\^`\[\]]+',
    re.IGNORECASE
)

SCHEMA_REF_PATTERN = re.compile(r'\$ref\s*:\s*["\']?([^"\']+)["\']?')

DANGEROUS_PERMISSIONS = [
    "filesystem", "fs", "file_write", "file_read", "file_delete",
    "network", "http", "fetch", "request", "outbound",
    "credentials", "auth", "token", "secret", "key", "api_key",
    "exec", "execute", "run", "shell", "bash", "subprocess",
    "env", "environment", "variable", "secrets", "password",
]

REPORT_PATH = "SCHEMA_SCAN_REPORT.md"


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a query against the write_service query endpoint."""
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except requests.exceptions.RequestException as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service."""
    if not rows:
        return True
    try:
        response = requests.post(
            WRITE_URL,
            json={"table": table, "rows": rows},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Write to {table} failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute SQL via write_service execute endpoint."""
    try:
        response = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Execute failed: {e}")
        return False


def send_heartbeat() -> bool:
    """Send heartbeat to service_health table."""
    try:
        response = requests.post(
            WRITE_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.now(timezone.utc).isoformat()
                }
            },
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        log.warning(f"Heartbeat failed: {e}")
        return False


def check_single_instance() -> bool:
    """Ensure only one instance of this service is running."""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    try:
        if os.path.exists(pid_file):
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance is running with PID {old_pid}")
                return False
            except OSError:
                log.info(f"Stale PID file found, removing")
                os.remove(pid_file)
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.error(f"Failed to create PID file: {e}")
        return False


def fetch_tool_definitions(url: str) -> Dict[str, Any]:
    """Fetch tool definitions from a server's /tools endpoint."""
    try:
        tools_url = f"{url.rstrip('/')}/tools"
        response = requests.get(
            tools_url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "ZO-SENTINEL-Scanner/1.0"}
        )
        if response.status_code == 200:
            return response.json()
        return {}
    except requests.exceptions.RequestException as e:
        log.debug(f"Failed to fetch tools from {url}: {e}")
        return {}


def check_additional_properties(schema: Dict[str, Any], path: str = "") -> List[Dict[str, Any]]:
    """Detect additionalProperties: true which allows arbitrary input."""
    findings = []
    if not isinstance(schema, dict):
        return findings
    
    if schema.get("additionalProperties") is True:
        findings.append({
            "type": "additional_properties_true",
            "severity": "MEDIUM",
            "path": path or "root",
            "description": "Schema allows additional arbitrary properties",
            "schema_fragment": json.dumps(schema.get("additionalProperties", {}))
        })
    
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for prop_name, prop_schema in value.items():
                new_path = f"{path}.{prop_name}" if path else prop_name
                findings.extend(check_additional_properties(prop_schema, new_path))
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            new_path = f"{path}.{key}" if path else key
            findings.extend(check_additional_properties(value, new_path))
    
    return findings


def check_missing_validation(schema: Dict[str, Any], path: str = "") -> List[Dict[str, Any]]:
    """Check for string parameters without validation constraints."""
    findings = []
    if not isinstance(schema, dict):
        return findings
    
    schema_type = schema.get("type")
    
    if schema_type == "string":
        has_validation = any(
            key in schema for key in ["minLength", "maxLength", "pattern", "format", "enum"]
        )
        if not has_validation and schema.get("description"):
            findings.append({
                "type": "missing_string_validation",
                "severity": "MEDIUM",
                "path": path,
                "description": f"String parameter '{path}' has no length/pattern validation",
                "schema_fragment": json.dumps({"type": "string", "description": schema.get("description")})
            })
    
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for prop_name, prop_schema in value.items():
                new_path = f"{path}.{prop_name}" if path else prop_name
                findings.extend(check_missing_validation(prop_schema, new_path))
        elif key in ("items", "allOf", "anyOf", "oneOf") and isinstance(value, (dict, list)):
            if isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, dict):
                        findings.extend(check_missing_validation(item, f"{path}.{key}[{idx}]"))
            else:
                findings.extend(check_missing_validation(value, f"{path}.{key}"))
    
    return findings


def check_recursive_schema(schema: Dict[str, Any], parent_names: Set[str] = None, path: str = "") -> List[Dict[str, Any]]:
    """Detect recursive $ref patterns that could cause DoS."""
    findings = []
    if not isinstance(schema, dict):
        return findings
    
    if parent_names is None:
        parent_names = set()
    
    current_name = schema.get("name", "")
    if current_name:
        parent_names = parent_names.copy()
        parent_names.add(current_name)
    
    if "$ref" in schema:
        ref_value = schema.get("$ref", "")
        if ref_value in parent_names:
            findings.append({
                "type": "recursive_schema_reference",
                "severity": "HIGH",
                "path": path,
                "description": f"Schema contains recursive $ref to '{ref_value}' - potential DoS",
                "schema_fragment": json.dumps({"$ref": ref_value})
            })
    
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for prop_name, prop_schema in value.items():
                new_path = f"{path}.{prop_name}" if path else prop_name
                findings.extend(check_recursive_schema(prop_schema, parent_names.copy(), new_path))
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            new_path = f"{path}.{key}" if path else key
            findings.extend(check_recursive_schema(value, parent_names.copy(), new_path))
        elif key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    findings.extend(check_recursive_schema(item, parent_names.copy(), f"{path}.{key}[{idx}]"))
    
    return findings


def check_required_over_permissioned(schema: Dict[str, Any], path: str = "") -> Optional[Dict[str, Any]]:
    """Check if required params list is excessively long (>10)."""
    if not isinstance(schema, dict):
        return None
    
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    
    if len(required) > 10:
        return {
            "type": "over_permissioned",
            "severity": "CAUTION",
            "path": path,
            "description": f"Schema has {len(required)} required parameters - may be over-permissioned",
            "required_count": len(required),
            "required_params": required
        }
    
    return None


def check_ssrf_in_description(tool: Dict[str, Any], server_url: str) -> List[Dict[str, Any]]:
    """Detect URL patterns in tool description with potential SSRF risk."""
    findings = []
    description = tool.get("description", "") or ""
    input_schema = tool.get("inputSchema", {})
    properties = input_schema.get("properties", {}) if isinstance(input_schema, dict) else {}
    
    url_in_desc = URL_IN_DESCRIPTION_PATTERN.findall(description)
    
    if not url_in_desc:
        return findings
    
    for prop_name, prop_schema in properties.items():
        prop_type = prop_schema.get("type", "")
        prop_desc = prop_schema.get("description", "") or ""
        
        if prop_type in ("string", "object") or "url" in prop_name.lower() or "endpoint" in prop_name.lower():
            if any(keyword in prop_desc.lower() for keyword in ["user", "input", "provided", "custom", "dynamic", "config"]):
                for url_match in url_in_desc:
                    findings.append({
                        "type": "ssrf_risk",
                        "severity": "HIGH",
                        "path": f"{tool.get('name', 'unknown')}.{prop_name}",
                        "description": f"Tool description contains URL with user-controlled input parameter '{prop_name}'",
                        "evidence": {
                            "url_in_description": url_match,
                            "user_controlled_param": prop_name,
                            "parameter_description": prop_desc
                        }
                    })
                break
    
    return findings


def check_internal_probe_defaults(schema: Dict[str, Any], tool_name: str, server_url: str) -> List[Dict[str, Any]]:
    """Detect default values containing IPs or internal hostnames."""
    findings = []
    if not isinstance(schema, dict):
        return findings
    
    def check_value(value: Any, path: str, default_key: str):
        if not isinstance(value, str):
            return
        
        if IP_PATTERN.search(value):
            findings.append({
                "type": "internal_probe",
                "severity": "CRITICAL",
                "path": f"{tool_name}.{path}",
                "description": f"Default value contains IP address: {value}",
                "evidence": {
                    "property_path": path,
                    "default_value": value,
                    "server_url": server_url
                }
            })
        
        for pattern in INTERNAL_HOSTNAME_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                findings.append({
                    "type": "internal_probe",
                    "severity": "CRITICAL",
                    "path": f"{tool_name}.{path}",
                    "description": f"Default value contains internal hostname: {value}",
                    "evidence": {
                        "property_path": path,
                        "default_value": value,
                        "server_url": server_url,
                        "matched_pattern": pattern
                    }
                })
                break
    
    if "default" in schema:
        check_value(schema["default"], path or "default", "default")
    
    if "examples" in schema:
        for idx, example in enumerate(schema.get("examples", [])):
            check_value(example, f"{path}.examples[{idx}]", "examples")
    
    if "enum" in schema:
        for idx, enum_val in enumerate(schema.get("enum", [])):
            check_value(enum_val, f"{path}.enum[{idx}]", "enum")
    
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            for prop_name, prop_schema in value.items():
                new_path = f"{path}.{prop_name}" if path else prop_name
                findings.extend(check_internal_probe_defaults(prop_schema, tool_name, server_url))
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            new_path = f"{path}.{key}" if path else key
            findings.extend(check_internal_probe_defaults(value, tool_name, server_url))
    
    return findings


def analyze_tool_schema(tool: Dict[str, Any], server_id: str, server_url: str) -> List[Dict[str, Any]]:
    """Perform full schema analysis on a single tool."""
    findings = []
    tool_name = tool.get("name", "unknown")
    input_schema = tool.get("inputSchema", {})
    
    if not isinstance(input_schema, dict):
        return findings
    
    findings.extend(check_additional_properties(input_schema, tool_name))
    findings.extend(check_missing_validation(input_schema, tool_name))
    findings.extend(check_recursive_schema(input_schema, path=tool_name))
    findings.extend(check_ssrf_in_description(tool, server_url))
    findings.extend(check_internal_probe_defaults(input_schema, tool_name, server_url))
    
    over_perm = check_required_over_permissioned(input_schema, tool_name)
    if over_perm:
        findings.append(over_perm)
    
    return findings


def get_registry_servers() -> List[Dict[str, Any]]:
    """Fetch all servers from the registry."""
    sql = f"""
    SELECT server_id, name, url, registry_source
    FROM {REGISTRY_TABLE}
    WHERE url IS NOT NULL AND url != ''
    """
    return ws_query(sql)


def compute_tool_description_safety_score(findings: List[Dict[str, Any]]) -> float:
    """Calculate tool_description_safety score based on findings."""
    if not findings:
        return 100.0
    
    score = 100.0
    severity_deductions = {
        "CRITICAL": 40,
        "HIGH": 25,
        "MEDIUM": 15,
        "CAUTION": 5,
        "LOW": 2
    }
    
    seen_severities = set()
    for finding in findings:
        severity = finding.get("severity", "LOW")
        if severity not in seen_severities:
            score -= severity_deductions.get(severity.upper(), 2)
            seen_severities.add(severity)
    
    return max(0.0, min(100.0, score))


def compute_permission_scope_score(findings: List[Dict[str, Any]], tool_count: int) -> float:
    """Calculate permission_scope score based on findings and tool count."""
    if tool_count == 0:
        return 100.0
    
    dangerous_findings = [f for f in findings if f.get("type") in [
        "additional_properties_true",
        "recursive_schema_reference",
        "over_permissioned"
    ]]
    
    score = 100.0
    deduction_per_dangerous = 15
    
    score -= min(60, len(dangerous_findings) * deduction_per_dangerous)
    
    return max(0.0, min(100.0, score))


def ensure_tables() -> bool:
    """Ensure required tables exist."""
    signal_scores_table = f"""
    CREATE TABLE IF NOT EXISTS {SIGNAL_TABLE} (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        signal_name VARCHAR NOT NULL,
        score       REAL,
        evidence    TEXT,
        scored_at   TIMESTAMPTZ DEFAULT now()
    )
    """
    
    threat_table = f"""
    CREATE TABLE IF NOT EXISTS {THREAT_TABLE} (
        id          BIGINT PRIMARY KEY,
        server_id   VARCHAR NOT NULL,
        threat_type VARCHAR,
        evidence    TEXT,
        severity    VARCHAR,
        reported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    
    return ws_execute(signal_scores_table) and ws_execute(threat_table)


def generate_report(server_findings: Dict[str, List[Dict[str, Any]]], all_servers: int) -> str:
    """Generate markdown report of scan findings."""
    lines = [
        "# Schema Deep Scan Report",
        f"\nGenerated: {datetime.now(timezone.utc).isoformat()}",
        f"\nTotal Servers Scanned: {all_servers}",
        ""
    ]
    
    critical_count = 0
    high_count = 0
    medium_count = 0
    caution_count = 0
    
    for server_id, findings in server_findings.items():
        for finding in findings:
            severity = finding.get("severity", "UNKNOWN")
            if severity == "CRITICAL":
                critical_count += 1
            elif severity == "HIGH":
                high_count += 1
            elif severity == "MEDIUM":
                medium_count += 1
            elif severity == "CAUTION":
                caution_count += 1
    
    lines.extend([
        "## Summary",
        "",
        f"- CRITICAL Issues: {critical_count}",
        f"- HIGH Issues: {high_count}",
        f"- MEDIUM Issues: {medium_count}",
        f"- CAUTION Issues: {caution_count}",
        ""
    ])
    
    lines.extend([
        "## Severity Legend",
        "",
        "| Severity | Description |",
        "|----------|-------------|",
        "| CRITICAL | Internal probe vulnerabilities, immediate action required |",
        "| HIGH | SSRF risks, recursive schema DoS potential |",
        "| MEDIUM | Additional properties allowed, missing validation |",
        "| CAUTION | Over-permissioned tools, review recommended |",
        ""
    ])
    
    finding_types = {}
    for server_id, findings in server_findings.items():
        for finding in findings:
            ftype = finding.get("type", "unknown")
            if ftype not in finding_types:
                finding_types[ftype] = []
            finding_types[ftype].append((server_id, finding))
    
    if finding_types:
        lines.extend([
            "## Findings by Type",
            ""
        ])
        
        for ftype, entries in sorted(finding_types.items(), key=lambda x: -len(x[1])):
            lines.extend([
                f"### {ftype.replace('_', ' ').title()}",
                "",
                f"Total: {len(entries)} occurrences",
                ""
            ])
            
            for server_id, finding in entries[:5]:
                severity = finding.get("severity", "UNKNOWN")
                description = finding.get("description", "No description")
                path = finding.get("path", "unknown")
                
                lines.extend([
                    f"**Server:** `{server_id}`",
                    f"**Severity:** {severity}",
                    f"**Path:** `{path}`",
                    f"**Description:** {description}",
                    ""
                ])
    
    servers_with_issues = len([f for f in server_findings.values() if f])
    if servers_with_issues > 0:
        lines.extend([
            "## Affected Servers",
            "",
            f"{servers_with_issues} out of {all_servers} servers have schema issues.",
            ""
        ])
    
    return "\n".join(lines)


def run_scan_cycle() -> bool:
    """Execute one complete scan cycle."""
    log.info("Starting schema deep scan cycle")
    
    servers = get_registry_servers()
    log.info(f"Found {len(servers)} servers to scan")
    
    all_findings = {}
    threat_rows = []
    signal_rows = []
    threat_id = int(time.time() * 1000)
    signal_id = int(time.time() * 1000000)
    
    for server in servers:
        server_id = server.get("server_id", "")
        server_url = server.get("url", "")
        
        if not server_url:
            continue
        
        log.info(f"Scanning schema for server: {server_id} ({server_url})")
        
        tools_data = fetch_tool_definitions(server_url)
        tools = tools_data.get("tools", []) or tools_data.get("result", {}).get("tools", [])
        
        if not tools:
            tools = []
            if isinstance(tools_data, list):
                tools = tools_data
        
        findings = []
        for tool in tools:
            tool_findings = analyze_tool_schema(tool, server_id, server_url)
            findings.extend(tool_findings)
        
        all_findings[server_id] = findings
        
        tool_count = len(tools)
        desc_safety = compute_tool_description_safety_score(findings)
        perm_scope = compute_permission_scope_score(findings, tool_count)
        
        signal_rows.append({
            "server_id": server_id,
            "signal_name": "tool_description_safety",
            "score": desc_safety,
            "evidence": json.dumps({
                "tool_count": tool_count,
                "findings_count": len(findings),
                "findings": findings[:5]
            })
        })
        
        signal_rows.append({
            "server_id": server_id,
            "signal_name": "permission_scope",
            "score": perm_scope,
            "evidence": json.dumps({
                "tool_count": tool_count,
                "dangerous_findings": len([f for f in findings if f.get("type") in [
                    "additional_properties_true", "recursive_schema_reference", "over_permissioned"
                ]])
            })
        })
        
        for finding in findings:
            threat_rows.append({
                "server_id": server_id,
                "threat_type": finding.get("type", "unknown"),
                "evidence": json.dumps(finding),
                "severity": finding.get("severity", "UNKNOWN")
            })
    
    if signal_rows:
        ws_write(SIGNAL_TABLE, signal_rows)
        log.info(f"Wrote {len(signal_rows)} signal scores")
    
    if threat_rows:
        ws_write(THREAT_TABLE, threat_rows)
        log.info(f"Wrote {len(threat_rows)} threat associations")
    
    report = generate_report(all_findings, len(servers))
    try:
        with open(REPORT_PATH, 'w') as f:
            f.write(report)
        log.info(f"Written scan report to {REPORT_PATH}")
    except IOError as e:
        log.error(f"Failed to write report: {e}")
    
    log.info("Schema deep scan cycle completed")
    return True


def run():
    """Main run loop for the daemon."""
    if not check_single_instance():
        log.error("Cannot start: another instance is running")
        return
    
    ensure_tables()
    log.info(f"Starting {SERVICE_NAME} daemon")
    
    run_scan_cycle()
    
    while True:
        time.sleep(POLL_INTERVAL)
        run_scan_cycle()


if __name__ == "__main__":
    run()