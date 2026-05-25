#!/usr/bin/env python3
"""
mcp_impersonation_detector.py -- ZO-SENTINEL MCP impersonation and namespace confusion detector.
Detects namespace squatting, homoglyph attacks, prefix/suffix injection, reversed scope,
and hyphenation variants. Writes threats to mcp_threat_associations.
"""
import os
import re
import time
import hashlib
import logging
import requests
from typing import Dict, List, Any, Optional, Set, Tuple
from datetime import datetime, timezone
from urllib.parse import urlparse
import signal

log = logging.getLogger(__name__)

# Service endpoints
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 300
POLL_INTERVAL = 86400
SERVICE_NAME = "impersonation_detector"
PID_FILE = "/tmp/zo_sentinel_impersonation_detector.pid"


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Execute query via DuckDB write_service."""
    payload = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error(f"ws_query error: {e}")
        return []


def ws_write(table: str, rows: Any) -> bool:
    """Write rows to table via write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write error: {e}")
        return False


def send_heartbeat(service: str = SERVICE_NAME) -> bool:
    """Send heartbeat to service_health table."""
    return ws_write("service_health", {
        "service": service,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "status": "running"
    })


def check_single_instance() -> bool:
    """Ensure only one instance runs via PID file."""
    pid_dir = os.path.dirname(PID_FILE)
    if pid_dir and not os.path.exists(pid_dir):
        os.makedirs(pid_dir, exist_ok=True)
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            log.error(f"Instance already running with PID {old_pid}")
            return False
        except (ValueError, ProcessLookupError, OSError):
            log.info("Stale PID file found, removing")
            os.remove(PID_FILE)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        log.error(f"Cannot write PID file: {e}")
        return False


def remove_pid_file():
    """Remove PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass


def normalize_package_name(name: str) -> str:
    """Normalize package name for comparison: lowercase, strip, remove scope prefix."""
    if not name:
        return ""
    name = name.lower().strip()
    if name.startswith("@"):
        parts = name.split("/", 1)
        if len(parts) == 2:
            return parts[1]
    return name


def extract_scope(name: str) -> Optional[str]:
    """Extract scope from package name like @scope/name."""
    if not name:
        return None
    name = name.strip()
    if name.startswith("@"):
        parts = name.split("/", 1)
        if len(parts) == 2:
            return parts[0]
    return None


def extract_base_name(name: str) -> str:
    """Extract base name without scope or mcp prefix."""
    if not name:
        return ""
    name = name.lower().strip()
    if name.startswith("@"):
        parts = name.split("/", 1)
        name = parts[1] if len(parts) == 2 else name
    name = re.sub(r"^(mcp[-_]?(server[-_]?)?|server[-_]?)", "", name)
    return name


def generate_homoglyph_variants(name: str) -> Set[str]:
    """Generate homoglyph variants of a package name."""
    variants = set()
    if not name:
        return variants
    normalized = name.lower().strip()
    variants.add(normalized)
    homoglyph_map = {
        'o': ['0'],
        'l': ['1', 'i'],
        'i': ['1', 'l'],
        '0': ['o'],
        '1': ['l', 'i'],
        'rn': ['m'],
        'vv': ['w'],
        'w': ['vv'],
        'c': ['e'],
        'e': ['c'],
    }
    for char, replacements in homoglyph_map.items():
        for replacement in replacements:
            variant = normalized.replace(char, replacement)
            if variant != normalized:
                variants.add(variant)
    return variants


def compute_homoglyph_distance(name1: str, name2: str) -> float:
    """Compute similarity based on homoglyph substitutions."""
    if not name1 or not name2:
        return 0.0
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    if n1 == n2:
        return 1.0
    if len(n1) != len(n2):
        return 0.0
    diff_count = sum(1 for a, b in zip(n1, n2) if a != b)
    return max(0.0, 1.0 - (diff_count / len(n1)))


def check_namespace_squatting(servers: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict, float]]:
    """Detect namespace squatting: @scope/name vs scope-name (missing scope)."""
    threats = []
    scoped = {}
    unscoped = {}
    for server in servers:
        name = server.get("name", "")
        if not name:
            continue
        scope = extract_scope(name)
        base = extract_base_name(name)
        if scope:
            key = f"{scope}/{base}"
            if key not in scoped:
                scoped[key] = []
            scoped[key].append(server)
        else:
            unscoped[base] = server
    for key, scoped_servers in scoped.items():
        _, base = key.split("/", 1)
        if base in unscoped:
            unscoped_server = unscoped[base]
            for scoped_server in scoped_servers:
                similarity = 1.0
                confidence = 85.0
                threats.append((scoped_server, unscoped_server, confidence))
    return threats


def check_homoglyph_attacks(servers: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict, float]]:
    """Detect homoglyph attacks using similarity scoring."""
    threats = []
    for i, server in enumerate(servers):
        name_i = server.get("name", "")
        if not name_i:
            continue
        normalized_i = normalize_package_name(name_i)
        variants_i = generate_homoglyph_variants(normalized_i)
        for j, server_j in enumerate(servers):
            if i >= j:
                continue
            name_j = server_j.get("name", "")
            if not name_j:
                continue
            normalized_j = normalize_package_name(name_j)
            if normalized_j == normalized_i:
                continue
            if normalized_j in variants_i:
                distance = compute_homoglyph_distance(normalized_i, normalized_j)
                confidence = distance * 90.0
                threats.append((server, server_j, confidence))
            else:
                distance = compute_homoglyph_distance(normalized_i, normalized_j)
                if distance > 0.8:
                    confidence = distance * 80.0
                    threats.append((server, server_j, confidence))
    return threats


def check_prefix_suffix_injection(servers: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict, float]]:
    """Detect prefix/suffix injection: mcp-postgres-official vs mcp-postgres."""
    threats = []
    name_to_servers: Dict[str, List[Dict]] = {}
    for server in servers:
        name = server.get("name", "")
        if not name:
            continue
        base = extract_base_name(name)
        if base not in name_to_servers:
            name_to_servers[base] = []
        name_to_servers[base].append(server)
    for base, group in name_to_servers.items():
        if len(group) < 2:
            continue
        original_candidates = [s for s in group if len(extract_base_name(s.get("name", ""))) <= len(base) + 2]
        injected_candidates = [s for s in group if len(extract_base_name(s.get("name", ""))) > len(base) + 3]
        for original in original_candidates:
            for injected in injected_candidates:
                orig_name = original.get("name", "")
                inj_name = injected.get("name", "")
                if orig_name and inj_name:
                    extra_chars = len(inj_name) - len(orig_name)
                    if extra_chars > 0:
                        confidence = min(75.0, 60.0 + extra_chars * 3)
                        threats.append((injected, original, confidence))
    return threats


def check_reversed_scope(servers: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict, float]]:
    """Detect reversed scope: server-filesystem-mcp vs mcp-server-filesystem."""
    threats = []
    name_index: Dict[str, List[Dict]] = {}
    for server in servers:
        name = server.get("name", "")
        if not name:
            continue
        normalized = normalize_package_name(name)
        if normalized not in name_index:
            name_index[normalized] = []
        name_index[normalized].append(server)
    reverse_patterns = [
        (r"^server-", "mcp-server-"),
        (r"^mcp-", "server-"),
    ]
    for server in servers:
        name = server.get("name", "")
        if not name:
            continue
        for pattern, replacement in reverse_patterns:
            if re.match(pattern, name, re.IGNORECASE):
                reversed_name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
                reversed_normalized = normalize_package_name(reversed_name)
                if reversed_normalized in name_index:
                    for target in name_index[reversed_normalized]:
                        if target != server:
                            confidence = 95.0
                            threats.append((server, target, confidence))
    return threats


def check_hyphenation_variants(servers: List[Dict[str, Any]]) -> List[Tuple[Dict, Dict, float]]:
    """Detect hyphenation variants: filesystem vs file-system vs file_system."""
    threats = []
    variants_map: Dict[str, List[Dict]] = {}
    for server in servers:
        name = server.get("name", "")
        if not name:
            continue
        base = extract_base_name(name)
        normalized_variant = re.sub(r"[-_]", "", base)
        if normalized_variant not in variants_map:
            variants_map[normalized_variant] = []
        variants_map[normalized_variant].append((server, name))
    for variant_key, group in variants_map.items():
        if len(group) < 2:
            continue
        seen_patterns = {}
        for server, name in group:
            has_hyphen = "-" in name
            has_underscore = "_" in name
            pattern = ("hyphen" if has_hyphen else "none") + ("_underscore" if has_underscore else "")
            if pattern not in seen_patterns:
                seen_patterns[pattern] = []
            seen_patterns[pattern].append(server)
        if len(seen_patterns) > 1:
            all_servers = [s for s, _ in group]
            for i, (suspect, _) in enumerate(group):
                if i % 2 == 0:
                    continue
                for original in all_servers[:i] if i > 0 else all_servers[i+1:]:
                    confidence = 70.0
                    threats.append((suspect, original, confidence))
    return threats


def get_downloads(server: Dict[str, Any]) -> int:
    """Extract download count from server metadata."""
    description = server.get("description", "") or ""
    match = re.search(r"(\d[\d,]*(?:\.\d+)?[kmb]?)", description, re.IGNORECASE)
    if match:
        value = match.group(1).lower()
        try:
            multiplier = 1
            if value.endswith("k"):
                multiplier = 1000
                value = value[:-1]
            elif value.endswith("m"):
                multiplier = 1000000
                value = value[:-1]
            elif value.endswith("b"):
                multiplier = 1000000000
                value = value[:-1]
            value = value.replace(",", "")
            return int(float(value) * multiplier)
        except (ValueError, ArithmeticError):
            pass
    return 0


def check_already_reported(server_id: str, threat_type: str) -> bool:
    """Check if this impersonation threat was already reported recently."""
    query = """
    SELECT id FROM mcp_threat_associations 
    WHERE server_id = ? AND threat_type = ? 
    AND reported_at > now() - INTERVAL '7 days'
    LIMIT 1
    """
    results = ws_query(query, {"p1": server_id, "p2": threat_type})
    return len(results) > 0


def report_impersonation_threat(suspect: Dict[str, Any], target: Dict[str, Any], confidence: float, detection_type: str) -> bool:
    """Report impersonation threat to mcp_threat_associations."""
    suspect_id = suspect.get("server_id", "")
    target_id = target.get("server_id", "")
    suspect_name = suspect.get("name", "")
    target_name = target.get("name", "")
    if check_already_reported(suspect_id, "impersonation_attempt"):
        return False
    suspect_downloads = get_downloads(suspect)
    target_downloads = get_downloads(target)
    if suspect_downloads > 0 and target_downloads > 0 and suspect_downloads >= target_downloads:
        log.info(f"Skipping {suspect_name}: has >= downloads ({suspect_downloads}) than target ({target_downloads})")
        return False
    evidence = {
        "detection_type": detection_type,
        "suspect_name": suspect_name,
        "target_name": target_name,
        "suspect_id": suspect_id,
        "target_id": target_id,
        "confidence_score": confidence
    }
    row = {
        "server_id": suspect_id,
        "threat_type": "impersonation_attempt",
        "evidence": str(evidence),
        "severity": "HIGH" if confidence > 85 else "MEDIUM",
        "reported_at": datetime.now(timezone.utc).isoformat()
    }
    success = ws_write("mcp_threat_associations", row)
    if success:
        log.warning(f"IMP impersonation detected: {suspect_name} -> {target_name} (confidence: {confidence:.1f})")
    return success


def fetch_all_servers() -> List[Dict[str, Any]]:
    """Fetch all servers from registry."""
    query = """
    SELECT id, server_id, name, registry_source, url, description, trust_score, verdict
    FROM mcp_server_registry
    WHERE name IS NOT NULL AND name != ''
    """
    return ws_query(query)


def detect_impersonation_threats(servers: List[Dict[str, Any]]) -> int:
    """Run all impersonation detection algorithms and report threats."""
    total_threats = 0
    confidence_threshold = 70.0
    log.info(f"Checking {len(servers)} servers for impersonation threats...")
    namespace_threats = check_namespace_squatting(servers)
    log.info(f"Found {len(namespace_threats)} namespace squatting candidates")
    for suspect, target, confidence in namespace_threats:
        if confidence >= confidence_threshold:
            if report_impersonation_threat(suspect, target, confidence, "namespace_squatting"):
                total_threats += 1
    homoglyph_threats = check_homoglyph_attacks(servers)
    log.info(f"Found {len(homoglyph_threats)} homoglyph attack candidates")
    for suspect, target, confidence in homoglyph_threats:
        if confidence >= confidence_threshold:
            if report_impersonation_threat(suspect, target, confidence, "homoglyph_attack"):
                total_threats += 1
    prefix_suffix_threats = check_prefix_suffix_injection(servers)
    log.info(f"Found {len(prefix_suffix_threats)} prefix/suffix injection candidates")
    for suspect, target, confidence in prefix_suffix_threats:
        if confidence >= confidence_threshold:
            if report_impersonation_threat(suspect, target, confidence, "prefix_suffix_injection"):
                total_threats += 1
    reversed_threats = check_reversed_scope(servers)
    log.info(f"Found {len(reversed_threats)} reversed scope candidates")
    for suspect, target, confidence in reversed_threats:
        if confidence >= confidence_threshold:
            if report_impersonation_threat(suspect, target, confidence, "reversed_scope"):
                total_threats += 1
    hyphenation_threats = check_hyphenation_variants(servers)
    log.info(f"Found {len(hyphenation_threats)} hyphenation variant candidates")
    for suspect, target, confidence in hyphenation_threats:
        if confidence >= confidence_threshold:
            if report_impersonation_threat(suspect, target, confidence, "hyphenation_variant"):
                total_threats += 1
    log.info(f"Total impersonation threats reported: {total_threats}")
    return total_threats


def run():
    """Main daemon loop."""
    if not check_single_instance():
        log.error("Cannot acquire lock, exiting")
        return
    def cleanup_handler(signum, frame):
        log.info("Received shutdown signal")
        remove_pid_file()
        exit(0)
    signal.signal(signal.SIGTERM, cleanup_handler)
    signal.signal(signal.SIGINT, cleanup_handler)
    log.info(f"Starting {SERVICE_NAME} daemon")
    send_heartbeat()
    last_cycle = 0
    while True:
        try:
            current_time = time.time()
            if current_time - last_cycle >= POLL_INTERVAL:
                log.info("Starting impersonation detection cycle")
                servers = fetch_all_servers()
                if servers:
                    threats_found = detect_impersonation_threats(servers)
                    log.info(f"Cycle complete: {threats_found} threats reported")
                else:
                    log.warning("No servers found in registry")
                last_cycle = current_time
            send_heartbeat()
        except Exception as e:
            log.error(f"Error in main loop: {e}")
        time.sleep(min(60, POLL_INTERVAL))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    run()