#!/usr/bin/env python3
"""
context_manipulation_detector.py -- ZO-SENTINEL context manipulation detection daemon.
Scans MCP servers for dangerous parameter patterns that could enable context injection,
credential harvesting, or unauthorized code execution.
"""
import os
import sys
import time
import json
import logging
import requests
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

SERVICE_NAME = "context_manipulation_detector"
WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772/write")
EXECUTE_URL = os.environ.get("EXECUTE_URL", "http://127.0.0.1:8772/execute")
QUERY_URL = os.environ.get("QUERY_URL", "http://127.0.0.1:8772/query")
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 21600
PID_FILE = "/tmp/zo_sentinel_context_manipulation_detector.pid"

log = logging.getLogger(__name__)

DANGEROUS_PARAM_NAMES = {
    "prompt", "instruction", "system", "context", "override",
    "command", "shell", "code", "script", "exec", "execute",
    "query", "input", "text", "message", "content"
}

DANGEROUS_ENUM_VALUES = [
    "ignore previous", "ignore all prior", "disregard",
    "override", "bypass", "jailbreak", "pretend you are",
    "act as if", "you are now", "system prompt"
]

HIGH_PARAM_COUNT_THRESHOLD = 10

INSTRUCTION_LIKE_PATTERNS = [
    r"ignore\s+(?:previous|all\s+prior)",
    r"disregard",
    r"override\s+(?:your\s+)?(?:system|builtin)",
    r"bypass\s+(?:safety|security)",
    r"pretend\s+you\s+are",
    r"act\s+as\s+(?:if\s+)?you\s+were",
    r"new\s+system\s+prompt",
    r"forget\s+(?:all\s+)?previous",
    r"do\s+not\s+follow\s+(?:your\s+)?(?:rules|guidelines)",
]


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            return False
        except OSError:
            log.warning(f"Stale PID file found for {old_pid}, removing")
            os.remove(PID_FILE)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Remove PID file on exit."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute a query via write_service."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", data.get("data", []))
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service."""
    if not rows:
        return True
    payload = {"table": table, "rows": rows}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed for {table}: {e}")
        return False


def send_heartbeat(healthy: bool = True, error: Optional[str] = None) -> bool:
    """Send heartbeat to write_service."""
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat(),
        "status": "healthy" if healthy else "error",
        "error": error
    }
    return ws_write("service_health", [row])


def fetch_tool_schema(url: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Fetch tool schema from MCP server /tools endpoint."""
    tools_url = url.rstrip("/") + "/tools"
    try:
        resp = requests.get(tools_url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        return None
    except requests.exceptions.Timeout:
        log.debug(f"Timeout fetching {tools_url}")
        return None
    except Exception as e:
        log.debug(f"Error fetching {tools_url}: {e}")
        return None


def parse_tool_properties(tool: Dict[str, Any]) -> Dict[str, Any]:
    """Extract parameter information from tool definition."""
    input_schema = tool.get("inputSchema", tool.get("input_schema", {}))
    if isinstance(input_schema, str):
        try:
            input_schema = json.loads(input_schema)
        except json.JSONDecodeError:
            return {}
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    return {
        "name": tool.get("name", "unknown"),
        "properties": properties,
        "required": required,
        "total_params": len(properties)
    }


def has_instruction_like_enum(enum_values: List[Any]) -> bool:
    """Check if enum values contain instruction-like strings."""
    for val in enum_values:
        if not isinstance(val, str):
            continue
        val_lower = val.lower()
        for pattern in INSTRUCTION_LIKE_PATTERNS:
            import re
            if re.search(pattern, val_lower):
                return True
        for dangerous in DANGEROUS_ENUM_VALUES:
            if dangerous in val_lower:
                return True
    return False


def find_dangerous_parameters(properties: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Analyze properties for dangerous patterns."""
    dangerous = []
    for param_name, param_def in properties.items():
        param_lower = param_name.lower()
        param_type = param_def.get("type", "unknown")
        findings = []
        if param_type == "string" and param_def.get("maxLength") is None:
            findings.append("unbounded_string")
        for dangerous_name in DANGEROUS_PARAM_NAMES:
            if dangerous_name in param_lower:
                findings.append(f"dangerous_param_name:{dangerous_name}")
                break
        enum_values = param_def.get("enum", [])
        if enum_values and has_instruction_like_enum(enum_values):
            findings.append("instruction_like_enum")
        if findings:
            dangerous.append({
                "name": param_name,
                "type": param_type,
                "findings": findings,
                "has_enum": bool(enum_values)
            })
    return dangerous


def calculate_blast_radius(tool_name: str, dangerous_params: List[Dict[str, Any]]) -> str:
    """Calculate blast radius classification."""
    param_names = {p["name"].lower() for p in dangerous_params}
    param_names_lower = {p["name"] for p in dangerous_params}
    if any(name in param_names for name in ["file_path", "filepath", "file", "path"]):
        if any(name in param_names for name in ["url", "uri", "endpoint", "download_url"]):
            return "CRITICAL"
    if any(name in param_names for name in ["command", "shell", "code", "script"]):
        return "CRITICAL"
    if any(name in param_names for name in ["exec", "execute", "run"]):
        return "CRITICAL"
    return "HIGH"


def analyze_tool_for_context_manipulation(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Full analysis of a single tool for context manipulation risks."""
    tool_info = parse_tool_properties(tool)
    name = tool_info["name"]
    properties = tool_info["properties"]
    total_params = tool_info["total_params"]
    dangerous_params = find_dangerous_parameters(properties)
    findings = []
    severity = "NONE"
    if total_params > HIGH_PARAM_COUNT_THRESHOLD:
        findings.append(f"high_param_count:{total_params}")
        severity = "HIGH"
    if dangerous_params:
        if severity != "CRITICAL":
            blast_radius = calculate_blast_radius(name, dangerous_params)
            if blast_radius == "CRITICAL":
                severity = "CRITICAL"
            elif blast_radius == "HIGH" and severity != "CRITICAL":
                severity = "HIGH"
        findings.extend([
            f"{p['name']}:{','.join(p['findings'])}"
            for p in dangerous_params
        ])
    if not findings:
        return None
    return {
        "tool_name": name,
        "severity": severity,
        "total_params": total_params,
        "dangerous_param_count": len(dangerous_params),
        "findings": findings
    }


def get_registered_servers() -> List[Dict[str, Any]]:
    """Fetch all MCP servers from registry."""
    sql = """
    SELECT server_id, name, url, description
    FROM mcp_server_registry
    WHERE url IS NOT NULL AND url != ''
    ORDER BY last_seen DESC
    """
    return ws_query(sql)


def get_previous_findings(server_id: str) -> Set[str]:
    """Get previous threat findings for deduplication."""
    sql = """
    SELECT threat_type, evidence
    FROM mcp_threat_associations
    WHERE server_id = ? AND reported_at > now() - interval '7 days'
    """
    rows = ws_query(sql, {"server_id": server_id})
    findings = set()
    for row in rows:
        findings.add(f"{row.get('threat_type', '')}:{row.get('evidence', '')}")
    return findings


def record_threat_associations(server_id: str, tool_results: List[Dict[str, Any]]) -> int:
    """Record threat associations to database."""
    rows = []
    for result in tool_results:
        if result["severity"] in ("CRITICAL", "HIGH"):
            rows.append({
                "server_id": server_id,
                "threat_type": f"context_manipulation:{result['severity']}",
                "evidence": json.dumps({
                    "tool": result["tool_name"],
                    "findings": result["findings"],
                    "total_params": result["total_params"],
                    "dangerous_params": result["dangerous_param_count"]
                }),
                "severity": result["severity"],
                "reported_at": datetime.utcnow().isoformat()
            })
    if rows:
        ws_write("mcp_threat_associations", rows)
    return len(rows)


def record_signal_scores(server_id: str, tool_results: List[Dict[str, Any]]) -> int:
    """Record permission_scope signal scores."""
    critical_count = sum(1 for r in tool_results if r["severity"] == "CRITICAL")
    high_count = sum(1 for r in tool_results if r["severity"] == "HIGH")
    total_findings = len(tool_results)
    if total_findings == 0:
        return 0
    score = min(100.0, (critical_count * 50) + (high_count * 25) + (total_findings * 5))
    evidence = json.dumps({
        "tools_scanned": total_findings,
        "critical_threats": critical_count,
        "high_threats": high_count
    })
    row = {
        "server_id": server_id,
        "signal_name": "permission_scope",
        "score": score,
        "evidence": evidence,
        "scored_at": datetime.utcnow().isoformat()
    }
    ws_write("mcp_signal_scores", [row])
    return 1


def scan_server_for_context_manipulation(server: Dict[str, Any]) -> Dict[str, Any]:
    """Scan a single server for context manipulation vulnerabilities."""
    server_id = server.get("server_id", "")
    url = server.get("url", "")
    if not url:
        return {"server_id": server_id, "status": "skip", "reason": "no_url"}
    schema = fetch_tool_schema(url)
    if not schema:
        return {"server_id": server_id, "status": "error", "reason": "fetch_failed"}
    tools = schema if isinstance(schema, list) else schema.get("tools", [])
    if not tools:
        tools = schema.get("result", [])
    if not isinstance(tools, list):
        tools = []
    tool_results = []
    for tool in tools:
        result = analyze_tool_for_context_manipulation(tool)
        if result:
            tool_results.append(result)
    if tool_results:
        record_threat_associations(server_id, tool_results)
        record_signal_scores(server_id, tool_results)
    return {
        "server_id": server_id,
        "status": "complete",
        "tools_found": len(tools),
        "risks_found": len(tool_results),
        "risks": tool_results
    }


def run_cycle() -> Dict[str, Any]:
    """Execute one scan cycle across all servers."""
    log.info("Starting context manipulation scan cycle")
    servers = get_registered_servers()
    log.info(f"Found {len(servers)} servers to scan")
    results = []
    critical_count = 0
    high_count = 0
    for server in servers:
        try:
            result = scan_server_for_context_manipulation(server)
            results.append(result)
            for r in result.get("risks", []):
                if r["severity"] == "CRITICAL":
                    critical_count += 1
                elif r["severity"] == "HIGH":
                    high_count += 1
        except Exception as e:
            log.error(f"Error scanning server {server.get('server_id')}: {e}")
            results.append({
                "server_id": server.get("server_id", "unknown"),
                "status": "error",
                "reason": str(e)
            })
    summary = {
        "servers_scanned": len(results),
        "critical_findings": critical_count,
        "high_findings": high_count
    }
    log.info(f"Scan complete: {summary}")
    return summary


def heartbeat_loop():
    """Background heartbeat thread."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.error(f"Heartbeat failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main run loop for the daemon."""
    if not check_single_instance():
        sys.exit(1)
    try:
        log.info(f"Starting {SERVICE_NAME} daemon")
        send_heartbeat(healthy=True)
        while True:
            try:
                run_cycle()
            except Exception as e:
                log.error(f"Cycle failed: {e}")
                send_heartbeat(healthy=False, error=str(e))
            log.info(f"Sleeping for {POLL_INTERVAL}s until next cycle")
            time.sleep(POLL_INTERVAL)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()