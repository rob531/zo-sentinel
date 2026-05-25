#!/usr/bin/env python3
"""
manifest_blast_radius.py -- ZO-SENTINEL MCP manifest blast radius calculator.
Calculates blast radius scores based on tool permission combinations.
Polls registry for manifest data, computes dangerous combinations, and
updates signal scores and threat associations accordingly.
"""
import requests
import logging
import time
import re
import json
from typing import Dict, List, Any, Optional, Set
from datetime import datetime, timezone

log = logging.getLogger(__name__)

SERVICE_NAME = "manifest_blast_radius"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"

HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 21600
FETCH_TIMEOUT = 5
BLAST_RADIUS_TABLE = "mcp_signal_scores"
THREAT_TABLE = "mcp_threat_associations"
REGISTRY_TABLE = "mcp_server_registry"

PERMISSION_KEYWORDS = [
    "filesystem", "fs", "file", "read", "write", "delete",
    "network", "http", "request", "fetch", "outbound",
    "credentials", "auth", "token", "secret", "key",
    "exec", "execute", "run",
    "shell", "bash", "cmd",
    "env", "environment", "vars", "variable",
    "process", "subprocess", "spawn"
]

DANGEROUS_PATTERNS = [
    (r"\b(file|filesystem|fs)\b.*\b(network|outbound|http)\b", "semantic_exfiltration"),
    (r"\b(exec|execute)\b.*\b(file|filesystem|credential|auth)\b", "code_execution_with_data_access"),
    (r"\b(shell|bash|cmd)\b.*\b(file|filesystem|credential|auth)\b", "code_execution_with_data_access"),
    (r"\b(credential|auth|token|secret|key)\b.*\b(network|outbound|http)\b", "credential_exfiltration"),
    (r"\b(env|environment|variable|vars)\b.*\b(network|outbound|http)\b", "credential_exfiltration"),
]

CATEGORY_SCORES = {
    "CRITICAL": 100,
    "HIGH": 75,
    "MEDIUM": 50,
    "LOW": 25,
    "MINIMAL": 10
}


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a query against the write_service query endpoint."""
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "error":
            log.error(f"Query error: {result.get('error', 'Unknown error')}")
            return []
        return result.get("data", [])
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service."""
    try:
        response = requests.post(
            WRITE_URL,
            json={
                "table": table,
                "rows": rows
            },
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") == "error":
            log.error(f"Write error: {result.get('error', 'Unknown error')}")
            return False
        return True
    except Exception as e:
        log.error(f"Write failed: {e}")
        return False


def send_heartbeat() -> bool:
    """Send a heartbeat to the write_service for service health monitoring."""
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
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")
        return False


def check_single_instance() -> bool:
    """Check if another instance is running and exit if so."""
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    try:
        with open(pid_file, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            import os
            os.kill(old_pid, 0)
            log.warning(f"Another instance (PID {old_pid}) is running. Exiting.")
            return False
        except OSError:
            pass
    except FileNotFoundError:
        pass

    with open(pid_file, 'w') as f:
        import os
        f.write(str(os.getpid()))
    return True


def fetch_manifest_tools(server_url: str, name: str) -> Dict[str, Any]:
    """Attempt to fetch tool definitions from various endpoints."""
    result = {
        "tool_names": [],
        "permission_keywords": [],
        "source": None,
        "raw_data": None,
        "success": False,
        "error": None
    }

    if not server_url:
        return result

    base_url = server_url.rstrip('/')

    endpoints_to_try = [
        f"{base_url}/tools",
        f"{base_url}/manifest",
        f"{base_url}/.well-known/mcp.json"
    ]

    for endpoint in endpoints_to_try:
        try:
            response = requests.get(endpoint, timeout=FETCH_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                result["source"] = endpoint
                result["raw_data"] = data

                if isinstance(data, dict):
                    if "tools" in data and isinstance(data["tools"], list):
                        result["tool_names"] = [
                            t.get("name", t.get("method", "")) 
                            for t in data["tools"] 
                            if isinstance(t, dict)
                        ]
                        result["success"] = True
                        break
                    elif "methods" in data:
                        result["tool_names"] = list(data["methods"].keys())
                        result["success"] = True
                        break

                if isinstance(data, list):
                    result["tool_names"] = [t.get("name", "") for t in data if isinstance(t, dict)]
                    result["success"] = True
                    break

        except requests.RequestException as e:
            log.debug(f"Failed to fetch {endpoint}: {e}")
            continue

    if not result["success"] and server_url:
        result = fetch_npm_manifest(server_url, name)

    return result


def fetch_npm_manifest(server_url: str, name: str) -> Dict[str, Any]:
    """Attempt to fetch tool definitions from npm package.json."""
    result = {
        "tool_names": [],
        "permission_keywords": [],
        "source": None,
        "raw_data": None,
        "success": False,
        "error": None
    }

    if not name:
        return result

    npm_patterns = [
        name.lstrip('@'),
        name.replace('/', '-'),
        name.split('/')[-1] if '/' in name else name
    ]

    for pkg_name in npm_patterns:
        if not pkg_name or len(pkg_name) < 3:
            continue

        npm_url = f"https://registry.npmjs.org/{pkg_name}/latest"
        try:
            response = requests.get(npm_url, timeout=FETCH_TIMEOUT)
            if response.status_code == 200:
                package_data = response.json()

                result["raw_data"] = package_data

                if "keywords" in package_data:
                    result["permission_keywords"] = [
                        kw.lower() for kw in package_data["keywords"]
                        if any(p in kw.lower() for p in PERMISSION_KEYWORDS)
                    ]

                if "scripts" in package_data:
                    scripts = package_data["scripts"]
                    if any(k in scripts for k in ["install", "postinstall", "preinstall"]):
                        result["permission_keywords"].extend(["install_script", "exec"])

                result["source"] = f"npm:{pkg_name}"
                result["success"] = True
                return result

        except requests.RequestException:
            continue

    return result


def extract_permissions_from_tools(tools: List[Dict[str, Any]]) -> List[str]:
    """Extract permission keywords from tool definitions."""
    permissions = []

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        tool_str = json.dumps(tool).lower()

        for keyword in PERMISSION_KEYWORDS:
            if keyword in tool_str:
                if keyword not in permissions:
                    permissions.append(keyword)

        if "inputSchema" in tool:
            schema = tool.get("inputSchema", {})
            if isinstance(schema, dict):
                props = schema.get("properties", {})
                for prop in props.keys():
                    prop_lower = prop.lower()
                    if any(kw in prop_lower for kw in PERMISSION_KEYWORDS):
                        if prop_lower not in permissions:
                            permissions.append(prop_lower)

    return permissions


def extract_readme_keywords(server_url: str, name: str) -> List[str]:
    """Fetch README and scan for permission-related keywords."""
    keywords = []

    if not name:
        return keywords

    npm_patterns = [
        name.replace('/', '-'),
        name.split('/')[-1] if '/' in name else name
    ]

    for pkg_name in npm_patterns:
        if not pkg_name or len(pkg_name) < 3:
            continue

        readme_url = f"https://raw.githubusercontent.com/{pkg_name}/main/README.md"
        try:
            response = requests.get(readme_url, timeout=FETCH_TIMEOUT)
            if response.status_code == 200:
                readme_content = response.text[:500].lower()

                for keyword in PERMISSION_KEYWORDS:
                    if keyword in readme_content:
                        if keyword not in keywords:
                            keywords.append(keyword)

                break

        except requests.RequestException:
            continue

    return keywords


def compute_blast_radius(permissions: List[str]) -> Dict[str, Any]:
    """Compute blast radius score and category based on permission combinations."""
    perm_set = set(p.lower() for p in permissions)

    has_filesystem = any(p in perm_set for p in ["filesystem", "fs", "file", "read"])
    has_network_out = any(p in perm_set for p in ["network", "outbound", "http", "request", "fetch"])
    has_exec = any(p in perm_set for p in ["exec", "execute", "run"])
    has_shell = any(p in perm_set for p in ["shell", "bash", "cmd"])
    has_credentials = any(p in perm_set for p in ["credentials", "auth", "token", "secret", "key"])
    has_env = any(p in perm_set for p in ["env", "environment", "vars", "variable"])
    has_process = any(p in perm_set for p in ["process", "subprocess", "spawn"])

    dangerous_combinations = []
    blast_radius_score = 0
    category = "MINIMAL"

    if has_filesystem and has_network_out:
        dangerous_combinations.append("semantic_exfiltration: filesystem + network_outbound")
        blast_radius_score = max(blast_radius_score, 100)
        category = "CRITICAL"

    if (has_exec or has_shell) and (has_filesystem or has_credentials):
        dangerous_combinations.append("code_execution_with_data_access: exec/shell + filesystem/credentials")
        blast_radius_score = max(blast_radius_score, 100)
        category = "CRITICAL"

    if (has_credentials or has_env) and has_network_out:
        if category != "CRITICAL":
            dangerous_combinations.append("credential_exfiltration: credentials/env + network_outbound")
            blast_radius_score = max(blast_radius_score, 75)
            category = "HIGH"

    if has_network_out and not (has_credentials or has_env or has_filesystem):
        if category not in ["CRITICAL", "HIGH"]:
            dangerous_combinations.append("data_egress: network_outbound only")
            blast_radius_score = max(blast_radius_score, 50)
            category = "MEDIUM"

    if has_filesystem and not (has_network_out or has_credentials or has_env):
        if category == "MINIMAL":
            dangerous_combinations.append("local_access: filesystem only")
            blast_radius_score = max(blast_radius_score, 25)
            category = "LOW"

    if not dangerous_combinations:
        dangerous_combinations.append("minimal_permissions: no dangerous combinations detected")
        blast_radius_score = 10
        category = "MINIMAL"

    signal_score = max(0, 100 - blast_radius_score)

    return {
        "blast_radius_score": blast_radius_score,
        "blast_radius_category": category,
        "signal_score": signal_score,
        "evidence": "; ".join(dangerous_combinations),
        "permissions_found": list(perm_set)
    }


def build_permission_scope_signal(permissions: List[str]) -> Dict[str, Any]:
    """Build permission_scope signal based on found permissions."""
    perm_set = set(p.lower() for p in permissions)

    scope_level = "minimal"
    if any(p in perm_set for p in ["exec", "shell", "subprocess"]):
        scope_level = "high"
    elif any(p in perm_set for p in ["filesystem", "credentials", "auth"]):
        scope_level = "medium"
    elif any(p in perm_set for p in ["network", "http", "fetch"]):
        scope_level = "low"
    elif perm_set:
        scope_level = "minimal"

    score = 100 if scope_level == "high" else 75 if scope_level == "medium" else 50 if scope_level == "low" else 100

    return {
        "signal_name": "permission_scope",
        "score": score,
        "evidence": f"Permission scope assessed as {scope_level}: {', '.join(sorted(perm_set))}"
    }


def get_registry_servers() -> List[Dict[str, Any]]:
    """Fetch all servers from the mcp_server_registry."""
    sql = f"""
    SELECT server_id, name, url, description
    FROM {REGISTRY_TABLE}
    WHERE url IS NOT NULL AND url != ''
    """
    return ws_query(sql)


def update_signal_scores(server_id: str, blast_radius_result: Dict[str, Any], permission_scope: Dict[str, Any]) -> None:
    """Update signal scores for a server."""
    rows = [
        {
            "server_id": server_id,
            "signal_name": "blast_radius",
            "score": blast_radius_result["signal_score"],
            "evidence": blast_radius_result["evidence"]
        },
        {
            "server_id": server_id,
            "signal_name": permission_scope["signal_name"],
            "score": permission_scope["score"],
            "evidence": permission_scope["evidence"]
        }
    ]

    ws_write(BLAST_RADIUS_TABLE, rows)


def create_threat_association(server_id: str, blast_radius_result: Dict[str, Any], tool_names: List[str]) -> None:
    """Create threat association for CRITICAL or HIGH blast radius servers."""
    category = blast_radius_result["blast_radius_category"]

    if category not in ["CRITICAL", "HIGH"]:
        return

    severity_map = {
        "CRITICAL": "critical",
        "HIGH": "high"
    }

    rows = [{
        "server_id": server_id,
        "threat_type": f"blast_radius_{blast_radius_result['blast_radius_score']}",
        "evidence": json.dumps({
            "category": category,
            "description": blast_radius_result["evidence"],
            "permissions_found": blast_radius_result.get("permissions_found", []),
            "tool_count": len(tool_names),
            "tool_names": tool_names[:20]
        }),
        "severity": severity_map.get(category, "medium")
    }]

    ws_write(THREAT_TABLE, rows)


def process_server(server: Dict[str, Any]) -> None:
    """Process a single server's manifest and compute blast radius."""
    server_id = server.get("server_id")
    url = server.get("url")
    name = server.get("name", "")

    if not server_id:
        log.warning("Server missing server_id, skipping")
        return

    log.info(f"Processing {server_id} ({name})")

    manifest_data = fetch_manifest_tools(url, name)

    tool_names = manifest_data.get("tool_names", [])
    permissions = manifest_data.get("permission_keywords", [])

    if manifest_data.get("raw_data") and not permissions:
        if "tools" in manifest_data["raw_data"] and isinstance(manifest_data["raw_data"]["tools"], list):
            permissions = extract_permissions_from_tools(manifest_data["raw_data"]["tools"])

    if not permissions and manifest_data.get("source", "").startswith("npm"):
        readme_keywords = extract_readme_keywords(url, name)
        permissions.extend(readme_keywords)

    blast_radius_result = compute_blast_radius(permissions)
    permission_scope = build_permission_scope_signal(permissions)

    log.info(
        f"  {server_id}: {blast_radius_result['blast_radius_category']} "
        f"(score={blast_radius_result['blast_radius_score']}) "
        f"permissions={permissions[:5]}..."
    )

    update_signal_scores(server_id, blast_radius_result, permission_scope)

    if blast_radius_result["blast_radius_category"] in ["CRITICAL", "HIGH"]:
        create_threat_association(server_id, blast_radius_result, tool_names)


def cycle() -> None:
    """Run one cycle of blast radius calculation for all servers."""
    log.info("Starting blast radius calculation cycle")

    servers = get_registry_servers()
    log.info(f"Found {len(servers)} servers to process")

    for server in servers:
        try:
            process_server(server)
        except Exception as e:
            log.error(f"Failed to process server {server.get('server_id', 'unknown')}: {e}")
            continue

    log.info("Blast radius calculation cycle complete")


def heartbeat_loop() -> None:
    """Background loop to send heartbeats."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run() -> None:
    """Main run function with daemon loop."""
    if not check_single_instance():
        return

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    log.info(f"{SERVICE_NAME} starting with {POLL_INTERVAL}s poll interval")

    while True:
        try:
            cycle()
        except Exception as e:
            log.error(f"Cycle failed: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()