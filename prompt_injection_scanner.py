#!/usr/bin/env python3
"""
prompt_injection_scanner.py -- ZO-SENTINEL passive prompt injection scanner daemon.
Scans tool descriptions from mcp_server_registry for injection patterns and
writes threat associations and signal scores.
"""
import os
import re
import base64
import logging
import time
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import requests

SERVICE_NAME = "prompt_injection_scanner"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 14400
TOOL_FETCH_TIMEOUT = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(SERVICE_NAME)

INJECTION_PATTERNS = [
    (r"<IMPORTANT>", "hidden_xml_tag"),
    (r"<script", "hidden_html_tag"),
    (r"<iframe", "hidden_html_tag"),
    (r"<object", "hidden_html_tag"),
    (r"<embed", "hidden_html_tag"),
    (r"ignore previous instructions", "system_prompt_override"),
    (r"ignore all prior", "system_prompt_override"),
    (r"disregard (?:any )?previous", "system_prompt_override"),
    (r"disregard system (?:prompt|instructions)", "system_prompt_override"),
    (r"system prompt:", "system_prompt_override"),
    (r"override (?:your |the )?(?:system|builtin)", "system_prompt_override"),
    (r"pretend you are", "system_prompt_override"),
    (r"act as if you were", "system_prompt_override"),
    (r"you are now", "system_prompt_override"),
    (r"new system prompt", "system_prompt_override"),
    (r"ignore the above", "system_prompt_override"),
    (r"forget (?:all )?previous", "system_prompt_override"),
    (r"disregard (?:all |any )?instructions", "system_prompt_override"),
    (r"do not follow (?:your |the )?(?:rules|guidelines)", "system_prompt_override"),
    (r"instead behave as", "system_prompt_override"),
    (r"bypass (?:safety|security|filter)", "system_prompt_override"),
    (r"jailbreak", "system_prompt_override"),
    (r"\\x00", "null_byte_injection"),
    (r"\x00", "null_byte_injection"),
]

INVISIBLE_UNICODE = [
    (r"\u200b", "zero_width_space"),
    (r"\u200c", "zero_width_joiner"),
    (r"\u200d", "zero_width_joiner"),
    (r"\u00ad", "soft_hyphen"),
    (r"\u2028", "line_separator"),
    (r"\u2029", "paragraph_separator"),
    (r"\ufeff", "byte_order_mark"),
]

BASE64_PATTERN = r"[A-Za-z0-9+/]{40,}={0,2}"

NESTED_CALL_PATTERNS = [
    (r"mcp\.call\s*\(", "mcp_call_syntax"),
    (r"await\s+this\.tools\.call", "nested_tool_invocation"),
    (r"\$\{.*tool.*\}", "template_injection"),
]

ALL_PATTERNS = INJECTION_PATTERNS + INVISIBLE_UNICODE + NESTED_CALL_PATTERNS

compiled_patterns = []
for pattern, pattern_type in ALL_PATTERNS:
    try:
        compiled_patterns.append((re.compile(pattern, re.IGNORECASE | re.MULTILINE), pattern_type))
    except re.error:
        logger.warning(f"Failed to compile pattern: {pattern}")

base64_re = re.compile(BASE64_PATTERN)

def check_single_instance() -> bool:
    pid_file = f"/tmp/{SERVICE_NAME}.pid"
    if os.path.exists(pid_file):
        with open(pid_file, "r") as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.warning(f"Another instance is running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            logger.info(f"Stale PID file found, removing")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    return True

def get_write_url() -> str:
    return WRITE_SERVICE_URL

def get_query_url() -> str:
    return QUERY_URL

def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        elif isinstance(data, list):
            return data
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        return []

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    if not rows:
        return True
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return False

def send_heartbeat() -> bool:
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={
                "table": "service_health",
                "rows": {
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.utcnow().isoformat()
                }
            },
            timeout=10
        )
        return response.status_code == 200
    except requests.exceptions.RequestException as e:
        logger.error(f"Heartbeat failed: {e}")
        return False

def fetch_tool_schema(url: str) -> Optional[str]:
    if not url:
        return None
    base_url = url.rstrip("/")
    tools_url = f"{base_url}/tools"
    try:
        response = requests.get(tools_url, timeout=TOOL_FETCH_TIMEOUT)
        if response.status_code == 200:
            return response.text
    except requests.exceptions.RequestException:
        pass
    schema_url = f"{base_url}/schema"
    try:
        response = requests.get(schema_url, timeout=TOOL_FETCH_TIMEOUT)
        if response.status_code == 200:
            return response.text
    except requests.exceptions.RequestException:
        pass
    return None

def scan_text_for_patterns(text: str) -> List[Tuple[str, str, int]]:
    findings = []
    if not text:
        return findings
    for pattern_re, pattern_type in compiled_patterns:
        for match in pattern_re.finditer(text):
            findings.append((pattern_type, match.group(), match.start()))
    return findings

def decode_and_scan_base64(text: str) -> List[Tuple[str, str, int]]:
    findings = []
    if not text:
        return findings
    for match in base64_re.finditer(text):
        try:
            decoded = base64.b64decode(match.group()).decode("utf-8", errors="ignore")
            decoded_findings = scan_text_for_patterns(decoded)
            for pattern_type, pattern_text, _ in decoded_findings:
                findings.append((f"base64_decoded_{pattern_type}", pattern_text, match.start()))
        except Exception:
            pass
    return findings

def scan_server(server_id: str, name: str, description: Optional[str], url: Optional[str]) -> List[Dict[str, Any]]:
    findings = []
    combined_text = description or ""
    if url:
        tool_schema = fetch_tool_schema(url)
        if tool_schema:
            combined_text += " " + tool_schema
    pattern_findings = scan_text_for_patterns(combined_text)
    base64_findings = decode_and_scan_base64(combined_text)
    all_findings = pattern_findings + base64_findings
    if all_findings:
        evidence_parts = []
        for pattern_type, match_text, pos in all_findings:
            evidence_parts.append(f"{pattern_type}: '{match_text[:50]}...' at pos {pos}")
        evidence = "; ".join(evidence_parts)
        findings.append({
            "server_id": server_id,
            "server_name": name,
            "threat_type": "prompt_injection",
            "evidence": evidence[:1000],
            "severity": "CRITICAL",
            "reported_at": datetime.utcnow().isoformat()
        })
    return findings

def get_servers_to_scan() -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, name, url, description, trust_score
    FROM mcp_server_registry
    ORDER BY last_seen DESC
    """
    return ws_query(sql)

def calculate_trust_score_penalty(threat_count: int) -> float:
    return min(threat_count * 30.0, 90.0)

def ws_execute(sql: str) -> bool:
    try:
        response = requests.post(
            EXECUTE_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_execute failed: {e}")
        return False

def update_signal_scores(server_id: str, findings_count: int, has_injection: bool) -> bool:
    if findings_count == 0:
        return True
    signal_name = "tool_description_safety"
    score = max(0.0, 100.0 - calculate_trust_score_penalty(findings_count))
    evidence = f"Found {findings_count} injection patterns"
    rows = [{
        "server_id": server_id,
        "signal_name": signal_name,
        "score": score,
        "evidence": evidence,
        "scored_at": datetime.utcnow().isoformat()
    }]
    return ws_write("mcp_signal_scores", rows)

def adjust_trust_score(server_id: str, threat_count: int) -> bool:
    if threat_count == 0:
        return True
    penalty = calculate_trust_score_penalty(threat_count)
    sql = f"""
    UPDATE mcp_server_registry
    SET trust_score = GREATEST(0, COALESCE(trust_score, 100) - {penalty}),
        last_assessed = NOW()
    WHERE server_id = '{server_id.replace("'", "''")}'
    """
    return ws_execute(sql)

def cycle() -> int:
    logger.info("Starting prompt injection scan cycle")
    servers = get_servers_to_scan()
    if not servers:
        logger.warning("No servers found to scan")
        return 0
    all_threat_findings = []
    signal_updates = 0
    for server in servers:
        server_id = server.get("server_id", "")
        name = server.get("name", "unknown")
        description = server.get("description")
        url = server.get("url")
        if not server_id:
            continue
        threats = scan_server(server_id, name, description, url)
        if threats:
            all_threat_findings.extend(threats)
            for threat in threats:
                update_signal_scores(server_id, 1, True)
                adjust_trust_score(server_id, 1)
                signal_updates += 1
    if all_threat_findings:
        if ws_write("mcp_threat_associations", all_threat_findings):
            logger.info(f"Wrote {len(all_threat_findings)} threat associations")
        else:
            logger.error("Failed to write threat associations")
    else:
        logger.info("No prompt injection patterns detected")
    send_heartbeat()
    return len(all_threat_findings)

def run():
    if not check_single_instance():
        logger.error("Another instance is running, exiting")
        return
    logger.info(f"Starting {SERVICE_NAME} daemon")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")
    while True:
        try:
            cycle()
        except Exception as e:
            logger.error(f"Cycle failed with exception: {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    run()