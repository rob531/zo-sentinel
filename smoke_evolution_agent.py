#!/usr/bin/env python3
"""
smoke_evolution_agent.py -- ZO-SENTINEL Self-improving smoke test evolution daemon.
Every 21600s (6h): reads failure patterns, updates anti-pattern lists, verifies syntax.
"""
import os
import sys
import ast
import json
import shutil
import logging
import requests
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

log = logging.getLogger(__name__)

SERVICE_NAME = "smoke_evolution_agent"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 21600

ZO_SENTINEL_PATH = Path("/home/workspace/zo_sentinel")
GENERATION_FAILURES_PATH = ZO_SENTINEL_PATH / "GENERATION_FAILURES.md"
SMOKE_TEST_PATH = ZO_SENTINEL_PATH / "tests" / "smoke_test.py"
BUILDER_PATH = ZO_SENTINEL_PATH / "zo_sentinel_builder.py"
EVOLUTION_LOG_PATH = ZO_SENTINEL_PATH / "SMOKE_EVOLUTION_LOG.md"

EXISTING_IMPORT_PATTERNS = [
    "sqlite3",
    "duckdb",
    "from fastapi import FastAPI",
    "concurrent.futures",
    "subprocess.Popen",
    "os.system",
    "eval(",
    "exec(",
    "pickle.load",
    "import yaml with",
]

EXISTING_LLM_MARKERS = [
    "Sure",
    "Certainly",
    "Of course",
    "Here's",
    "I'll",
    "Let me",
    "The following",
    "Simply",
    "Just",
    "Easy",
    "Obviously",
]

EXISTING_WIRING_PREFIXES = [
    "wiring:",
    "runtime:",
    "connection:",
    "timeout:",
]


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute read query via query endpoint."""
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as e:
        log.warning(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service using 'rows' not 'row'."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30,
            headers={"Content-Type": "application/json"}
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"ws_write failed: {e}")
        return False


def send_heartbeat() -> bool:
    """Send heartbeat to service_health."""
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "last_heartbeat": datetime.utcnow().isoformat()
    })


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    pid_file = Path("/tmp/smoke_evolution_agent.pid")
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance already running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            pass
    pid_file.write_text(str(os.getpid()))
    return True


def get_db_path() -> Path:
    """Get DuckDB path from env or default."""
    return Path(os.environ.get("ZO_SENTINEL_DB", "/tmp/zo_sentinel.duckdb"))


def read_generation_failures() -> List[str]:
    """Read failure reasons from GENERATION_FAILURES.md."""
    failures = []
    if not GENERATION_FAILURES_PATH.exists():
        log.warning(f"{GENERATION_FAILURES_PATH} not found")
        return failures
    
    try:
        content = GENERATION_FAILURES_PATH.read_text()
        for line in content.split("\n"):
            line = line.strip()
            if line.startswith("## Failure:") or line.startswith("- **"):
                reason = line.replace("## Failure:", "").replace("- **", "").strip()
                if reason:
                    failures.append(reason)
    except Exception as e:
        log.error(f"Failed to read GENERATION_FAILURES.md: {e}")
    
    return failures


def read_mesh_memory_failures() -> List[str]:
    """Query mesh_memory for smoke_fail entries in last 24h."""
    failures = []
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    
    sql = f"""
    SELECT content FROM mesh_memory 
    WHERE agent_id = 'zo_sentinel.smoke_fail' 
    AND memory_type = 'build_traceback'
    AND created_at >= '{cutoff}'
    """
    
    rows = ws_query(sql)
    for row in rows:
        content = row.get("content", "")
        if content:
            failures.append(content)
    
    return failures


def extract_import_patterns(text: str) -> List[str]:
    """Extract import patterns from text."""
    patterns = []
    import_pattern = re.compile(r'(?:from\s+[\w.]+\s+import|import\s+[\w.]+)')
    matches = import_pattern.findall(text)
    for match in matches:
        clean = match.strip()
        if clean and clean not in patterns:
            patterns.append(clean)
    return patterns


def extract_llm_markers(text: str) -> List[str]:
    """Extract LLM prose markers from text."""
    markers = []
    marker_patterns = [
        r'\bSure!?\b',
        r'\bCertainly\b',
        r'\bOf course\b',
        r'\bHere\'s\b',
        r"\bI'll\b",
        r'\bLet me\b',
        r'\bSimply\b',
        r'\bJust\b',
        r'\bEasy\b',
        r'\bObviously\b',
    ]
    for pat in marker_patterns:
        if re.search(pat, text, re.IGNORECASE):
            clean_pat = re.sub(r'[^\w\s]', '', pat).strip()
            if clean_pat not in markers:
                markers.append(clean_pat)
    return markers


def extract_wiring_patterns(text: str) -> List[str]:
    """Extract wiring/routing patterns from failure text."""
    patterns = []
    wiring_pattern = re.compile(r'(?:wiring:|runtime:|connection:|timeout:)\s*([^\n.]+)', re.IGNORECASE)
    matches = wiring_pattern.findall(text)
    for match in matches:
        clean = match.strip()
        if clean and clean not in patterns:
            patterns.append(clean)
    return patterns


def is_new_forbidden_import(pattern: str, existing: List[str]) -> bool:
    """Check if import pattern is new and forbidden."""
    pattern_lower = pattern.lower()
    for ex in existing:
        if ex.lower() in pattern_lower or pattern_lower in ex.lower():
            return False
    return True


def is_new_llm_marker(marker: str, existing: List[str]) -> bool:
    """Check if LLM marker is new."""
    marker_lower = marker.lower()
    for ex in existing:
        if ex.lower() == marker_lower:
            return False
    return True


def is_new_wiring_pattern(pattern: str, existing_patterns: List[str]) -> bool:
    """Check if wiring pattern is truly new."""
    pattern_lower = pattern.lower()
    for ex in existing_patterns:
        if pattern_lower in ex.lower() or ex.lower() in pattern_lower:
            return False
    return True


def get_smoke_antipatterns() -> List[Tuple[str, str]]:
    """Parse SMOKE_ANTIPATTERNS from smoke_test.py."""
    antipatterns = []
    if not SMOKE_TEST_PATH.exists():
        return antipatterns
    
    try:
        content = SMOKE_TEST_PATH.read_text()
        match = re.search(r'SMOKE_ANTIPATTERNS\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            list_content = match.group(1)
            tuple_pattern = re.compile(r'\(([^)]+)\)')
            for m in tuple_pattern.finditer(list_content):
                parts = [p.strip().strip('"\'') for p in m.group(1).split(',')]
                if len(parts) >= 1:
                    antipatterns.append((parts[0], parts[1] if len(parts) > 1 else ""))
    except Exception as e:
        log.error(f"Failed to parse SMOKE_ANTIPATTERNS: {e}")
    
    return antipatterns


def get_wiring_antipatterns() -> List[Tuple[str, str]]:
    """Parse WIRING_ANTIPATTERNS from zo_sentinel_builder.py."""
    antipatterns = []
    if not BUILDER_PATH.exists():
        return antipatterns
    
    try:
        content = BUILDER_PATH.read_text()
        match = re.search(r'WIRING_ANTIPATTERNS\s*=\s*\[(.*?)\]', content, re.DOTALL)
        if match:
            list_content = match.group(1)
            tuple_pattern = re.compile(r'\(([^)]+)\)')
            for m in tuple_pattern.finditer(list_content):
                parts = [p.strip().strip('"\'') for p in m.group(1).split(',')]
                if len(parts) >= 1:
                    antipatterns.append((parts[0], parts[1] if len(parts) > 1 else ""))
    except Exception as e:
        log.error(f"Failed to parse WIRING_ANTIPATTERNS: {e}")
    
    return antipatterns


def append_to_smoke_antipatterns(new_patterns: List[Tuple[str, str]]) -> bool:
    """Append new patterns to SMOKE_ANTIPATTERNS in smoke_test.py."""
    if not new_patterns or not SMOKE_TEST_PATH.exists():
        return True
    
    try:
        shutil.copy(str(SMOKE_TEST_PATH), str(SMOKE_TEST_PATH) + ".bak")
        content = SMOKE_TEST_PATH.read_text()
        
        for pattern, desc in new_patterns:
            escaped_pattern = pattern.replace("'", "\\'")
            new_entry = f'\n    ("{escaped_pattern}", "{desc}"),'
            
            if re.search(r'SMOKE_ANTIPATTERNS\s*=\s*\[', content):
                content = re.sub(
                    r'(SMOKE_ANTIPATTERNS\s*=\s*\[.*?\])',
                    r'\1' + new_entry,
                    content,
                    flags=re.DOTALL
                )
        
        with open(SMOKE_TEST_PATH, "w") as f:
            f.write(content)
        
        try:
            ast.parse(content)
            log.info(f"Updated SMOKE_ANTIPATTERNS with {len(new_patterns)} patterns")
            return True
        except SyntaxError as e:
            shutil.copy(str(SMOKE_TEST_PATH) + ".bak", str(SMOKE_TEST_PATH))
            log.error(f"Syntax check failed after update, restored backup: {e}")
            return False
    
    except Exception as e:
        log.error(f"Failed to update SMOKE_ANTIPATTERNS: {e}")
        return False


def append_to_wiring_antipatterns(new_patterns: List[Tuple[str, str]]) -> bool:
    """Append new patterns to WIRING_ANTIPATTERNS in zo_sentinel_builder.py."""
    if not new_patterns or not BUILDER_PATH.exists():
        return True
    
    try:
        shutil.copy(str(BUILDER_PATH), str(BUILDER_PATH) + ".bak")
        content = BUILDER_PATH.read_text()
        
        for pattern, desc in new_patterns:
            escaped_pattern = pattern.replace("'", "\\'")
            new_entry = f'\n    ("{escaped_pattern}", "{desc}"),'
            
            if re.search(r'WIRING_ANTIPATTERNS\s*=\s*\[', content):
                content = re.sub(
                    r'(WIRING_ANTIPATTERNS\s*=\s*\[.*?\])',
                    r'\1' + new_entry,
                    content,
                    flags=re.DOTALL
                )
        
        with open(BUILDER_PATH, "w") as f:
            f.write(content)
        
        try:
            ast.parse(content)
            log.info(f"Updated WIRING_ANTIPATTERNS with {len(new_patterns)} patterns")
            return True
        except SyntaxError as e:
            shutil.copy(str(BUILDER_PATH) + ".bak", str(BUILDER_PATH))
            log.error(f"Syntax check failed after update, restored backup: {e}")
            return False
    
    except Exception as e:
        log.error(f"Failed to update WIRING_ANTIPATTERNS: {e}")
        return False


def write_evolution_log(new_import_patterns: List, new_llm_patterns: List, 
                         new_wiring_patterns: List) -> None:
    """Write SMOKE_EVOLUTION_LOG.md with changes."""
    timestamp = datetime.utcnow().isoformat()
    
    existing_content = ""
    if EVOLUTION_LOG_PATH.exists():
        existing_content = EVOLUTION_LOG_PATH.read_text()
    
    log_entry = f"\n## Evolution Run: {timestamp}\n\n"
    
    if new_import_patterns:
        log_entry += "### New Forbidden Import Patterns\n"
        for p, desc in new_import_patterns:
            log_entry += f"- `{p}` - {desc}\n"
        log_entry += "\n"
    
    if new_llm_patterns:
        log_entry += "### New LLM Prose Markers\n"
        for p in new_llm_patterns:
            log_entry += f"- `{p}`\n"
        log_entry += "\n"
    
    if new_wiring_patterns:
        log_entry += "### New Wiring Patterns\n"
        for p in new_wiring_patterns:
            log_entry += f"- `{p}`\n"
        log_entry += "\n"
    
    if not new_import_patterns and not new_llm_patterns and not new_wiring_patterns:
        log_entry += "No new patterns detected.\n\n"
    
    with open(EVOLUTION_LOG_PATH, "a") as f:
        f.write(log_entry)
    
    log.info(f"Wrote evolution log to {EVOLUTION_LOG_PATH}")


def post_smoke_patterns_event(new_import_count: int, new_llm_count: int, 
                               new_wiring_count: int) -> None:
    """Post smoke_patterns_updated event to mesh_events."""
    total_new = new_import_count + new_llm_count + new_wiring_count
    if total_new == 0:
        return
    
    ws_write("mesh_events", {
        "event_type": "smoke_patterns_updated",
        "agent_id": SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat(),
        "new_import_patterns": new_import_count,
        "new_llm_markers": new_llm_count,
        "new_wiring_patterns": new_wiring_count,
        "total_new_patterns": total_new
    })


def scan_failures_for_patterns() -> Tuple[List[Tuple[str, str]], List[str], List[Tuple[str, str]]]:
    """Scan all failure sources and extract new patterns."""
    all_failures = []
    all_failures.extend(read_generation_failures())
    all_failures.extend(read_mesh_memory_failures())
    
    if not all_failures:
        log.info("No failures found to analyze")
        return [], [], []
    
    all_text = " ".join(all_failures)
    
    existing_smoke = get_smoke_antipatterns()
    existing_smoke_patterns = [p[0].lower() for p in existing_smoke]
    
    existing_wiring = get_wiring_antipatterns()
    existing_wiring_patterns = [p[0].lower() for p in existing_wiring]
    
    new_import_patterns = []
    new_llm_markers = []
    new_wiring_patterns = []
    
    imports = extract_import_patterns(all_text)
    for imp in imports:
        if is_new_forbidden_import(imp, EXISTING_IMPORT_PATTERNS):
            if is_new_forbidden_import(imp, existing_smoke_patterns):
                if (imp, "forbidden import") not in new_import_patterns:
                    new_import_patterns.append((imp, "forbidden import"))
    
    llm_found = extract_llm_markers(all_text)
    for marker in llm_found:
        if is_new_llm_marker(marker, EXISTING_LLM_MARKERS):
            if is_new_llm_marker(marker, existing_smoke_patterns):
                if marker not in new_llm_markers:
                    new_llm_markers.append(marker)
    
    wiring = extract_wiring_patterns(all_text)
    for wpat in wiring:
        if is_new_wiring_pattern(wpat, existing_wiring_patterns):
            if is_new_wiring_pattern(wpat, existing_smoke_patterns):
                if (wpat, "wiring anti-pattern") not in new_wiring_patterns:
                    new_wiring_patterns.append((wpat, "wiring anti-pattern"))
    
    return new_import_patterns, new_llm_markers, new_wiring_patterns


def cycle() -> int:
    """Main evolution cycle - returns number of new patterns added."""
    log.info("Starting smoke evolution cycle")
    
    new_imports, new_llm, new_wiring = scan_failures_for_patterns()
    
    smoke_updates = [(p, d) for p, d in new_imports]
    for marker in new_llm:
        smoke_updates.append((marker, "llm prose marker"))
    
    wiring_updates = [(p, d) for p, d in new_wiring]
    
    smoke_ok = append_to_smoke_antipatterns(smoke_updates)
    wiring_ok = append_to_wiring_antipatterns(wiring_updates)
    
    if smoke_ok and wiring_ok:
        total_new = len(new_imports) + len(new_llm) + len(new_wiring)
        write_evolution_log(new_imports, new_llm, new_wiring)
        post_smoke_patterns_event(len(new_imports), len(new_llm), len(new_wiring))
        log.info(f"Evolution cycle complete. {total_new} new patterns added.")
        return total_new
    else:
        log.error("Evolution cycle failed - syntax check or backup issue")
        return 0


def run() -> None:
    """Main run loop with heartbeat and evolution cycles."""
    if not check_single_instance():
        sys.exit(1)
    
    log.info(f"Starting {SERVICE_NAME}")
    
    import time
    
    last_evolution = 0
    
    while True:
        send_heartbeat()
        
        now = time.time()
        if now - last_evolution >= CYCLE_INTERVAL:
            try:
                cycle()
                last_evolution = now
            except Exception as e:
                log.error(f"Evolution cycle error: {e}")
        
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == "__main__":
    run()