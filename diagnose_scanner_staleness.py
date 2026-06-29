# deps: requests
"""
Diagnostic for mcp_scanner staleness (4h13m overdue).

Investigates:
- Last heartbeat in service_health (target_server_id='mcp_scanner')
- Scanner loop pattern and error handling (reads mcp_scanner.py source)
- write_service connectivity

Outputs a diagnostic identifying root cause (network, exception, config, timeout).
"""

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
_SCANNER_SOURCE = Path(__file__).parent / "mcp_scanner.py"


def _query(sql: str, params: list = None) -> list[dict]:
    """Execute a SELECT via write_service /query endpoint."""
    payload = {"sql": sql, "params": params or []}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10
        )
        resp.raise_for_status()
        return resp.json().get("rows", [])
    except Exception as exc:
        return [{"_error": str(exc)}]


def _execute(sql: str, params: list = None) -> bool:
    """Execute DML/DDL via write_service /execute endpoint."""
    payload = {"sql": sql, "params": params or [], "wait": True}
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/execute", json=payload, timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception:
        return False


def fetch_scanner_heartbeat() -> dict:
    """Fetch mcp_scanner last heartbeat from service_health."""
    sql = (
        "SELECT status, last_heartbeat, meta, timestamp "
        "FROM service_health "
        "WHERE target_server_id = ? "
        "ORDER BY timestamp DESC LIMIT 1"
    )
    rows = _query(sql, ["mcp_scanner"])
    if rows and "_error" not in rows[0]:
        return rows[0]
    return {}


def fetch_scanner_log_tail(n: int = 50) -> list[str]:
    """Read last N lines of the scanner log if available."""
    for path in [
        Path("/var/log/zo_sentinel/mcp_scanner.log"),
        Path("/home/workspace/zo_sentinel/logs/mcp_scanner.log"),
    ]:
        if path.exists():
            try:
                lines = path.read_text().splitlines()
                return lines[-n:]
            except Exception:
                pass
    return []


def check_process_alive() -> bool:
    """Check if mcp_scanner process is running."""
    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "mcp_scanner" in cmdline and "diagnose" not in cmdline:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        pass
    return False


def check_write_service_connectivity() -> dict:
    """Test write_service connectivity with latency measurement."""
    start = time.time()
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT 1 AS ping", "params": []},
            timeout=10,
        )
        latency_ms = int((time.time() - start) * 1000)
        return {
            "connected": resp.status_code == 200,
            "latency_ms": latency_ms,
            "status_code": resp.status_code,
        }
    except requests.ConnectionError:
        return {"connected": False, "latency_ms": -1, "error": "connection_refused"}
    except requests.Timeout:
        return {"connected": False, "latency_ms": -1, "error": "timeout"}
    except Exception as exc:
        return {"connected": False, "latency_ms": -1, "error": str(exc)}


def analyze_scanner_loop_pattern() -> dict:
    """Inspect mcp_scanner.py source for loop patterns and error handling."""
    result = {
        "has_run_function": False,
        "has_heartbeat_loop": False,
        "has_exception_handling": False,
        "has_external_api_calls": False,
        "heartbeat_interval_sec": None,
        "loop_sleep_sec": None,
        "potential_blocking_calls": [],
    }
    if not _SCANNER_SOURCE.exists():
        result["source_read_error"] = "mcp_scanner.py not found"
        return result

    try:
        source = _SCANNER_SOURCE.read_text()
    except Exception as exc:
        result["source_read_error"] = str(exc)
        return result

    # Detect key patterns
    result["has_run_function"] = bool(re.search(r"def run\s*\(", source))
    result["has_heartbeat_loop"] = bool(re.search(r"heartbeat", source, re.IGNORECASE))
    result["has_exception_handling"] = bool(re.search(r"except\s+\w+", source))
    result["has_external_api_calls"] = bool(
        re.search(r"requests\.(get|post)|urllib", source)
    )

    # Extract heartbeat interval
    hb_match = re.search(r"heartbeat.*?(\d+)", source[:2000], re.IGNORECASE)
    if hb_match:
        result["heartbeat_interval_sec"] = int(hb_match.group(1))

    # Detect sleep in loop (potential stuck scenario)
    sleep_match = re.search(r"time\.sleep\s*\(\s*(\d+)\s*\)", source)
    if sleep_match:
        result["loop_sleep_sec"] = int(sleep_match.group(1))

    # Detect blocking calls (requests without timeout)
    for line in source.splitlines():
        if re.search(r"requests\.(get|post)\s*\([^)]*\)(?!\s*,)", line):
            if "timeout" not in line:
                result["potential_blocking_calls"].append(line.strip()[:80])

    return result


def calculate_staleness(heartbeat_str: Optional[str]) -> dict:
    """Calculate staleness from ISO timestamp string."""
    if not heartbeat_str:
        return {"stale_seconds": -1, "stale_human": "unknown", "is_stale": True}

    try:
        dt = datetime.fromisoformat(heartbeat_str.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.utcnow()
        age = (now - dt).total_seconds()
        hours = int(age // 3600)
        minutes = int((age % 3600) // 60)
        return {
            "stale_seconds": int(age),
            "stale_human": f"{hours}h{minutes}m",
            "is_stale": age > 300,  # 5 min threshold
        }
    except Exception:
        return {"stale_seconds": -1, "stale_human": "parse_error", "is_stale": True}


def infer_root_cause(heartbeat_data: dict, log_lines: list, loop_analysis: dict, ws_conn: dict) -> dict:
    """Infer root cause from collected data."""
    diagnosis = {
        "root_cause_category": "unknown",
        "confidence": "low",
        "details": [],
        "human_action": [],
    }

    # Check write_service connectivity first
    if not ws_conn.get("connected"):
        diagnosis["root_cause_category"] = "network"
        diagnosis["confidence"] = "high"
        diagnosis["details"].append(f"write_service unreachable: {ws_conn.get('error', 'unknown')}")
        diagnosis["human_action"].append("Check write_service daemon is running on port 8772")
        diagnosis["human_action"].append("Verify firewall/network access to 127.0.0.1:8772")
        return diagnosis

    # Check heartbeat data
    if not heartbeat_data:
        diagnosis["root_cause_category"] = "config"
        diagnosis["confidence"] = "high"
        diagnosis["details"].append("No heartbeat record in service_health for mcp_scanner")
        diagnosis["human_action"].append("Check if mcp_scanner ever wrote a heartbeat")
        diagnosis["human_action"].append("Verify target_server_id='mcp_scanner' is correct")
        return diagnosis

    status = heartbeat_data.get("status", "")
    if status and status != "OK":
        diagnosis["root_cause_category"] = "exception"
        diagnosis["confidence"] = "high"
        meta = heartbeat_data.get("meta", {})
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                pass
        diagnosis["details"].append(f"Scanner status is '{status}' - indicates error state")
        if meta:
            diagnosis["details"].append(f"Meta: {json.dumps(meta)}")
        diagnosis["human_action"].append(f"Investigate scanner status={status}")
        diagnosis["human_action"].append("Check logs for exception traceback")

    # Check log for errors
    error_patterns = [
        (r"ConnectionError|Connection refused", "network"),
        (r"Timeout|timed out", "timeout"),
        (r"Exception|Error|Traceback", "exception"),
        (r"keyboard interrupt|signal|sigterm", "config"),
    ]
    for line in log_lines[-20:]:
        for pattern, category in error_patterns:
            if re.search(pattern, line, re.IGNORECASE):
                diagnosis["details"].append(f"Log error: {line.strip()[:120]}")
                if diagnosis["root_cause_category"] == "unknown":
                    diagnosis["root_cause_category"] = category
                    diagnosis["confidence"] = "medium"

    # Check for blocking calls without timeout
    if loop_analysis.get("potential_blocking_calls"):
        diagnosis["root_cause_category"] = "timeout"
        diagnosis["confidence"] = "medium"
        diagnosis["details"].append(
            f"Found requests calls without timeout: {len(loop_analysis['potential_blocking_calls'])} instances"
        )
        diagnosis["human_action"].append(
            "Scanner may hang on slow/unresponsive API - add timeout=10 to all requests calls"
        )

    # Check for missing heartbeat loop
    if not loop_analysis.get("has_heartbeat_loop"):
        diagnosis["root_cause_category"] = "config"
        diagnosis["confidence"] = "high"
        diagnosis["details"].append("Scanner source lacks heartbeat mechanism")
        diagnosis["human_action"].append("Verify mcp_scanner.py has heartbeat() call in run() loop")

    if diagnosis["root_cause_category"] == "unknown":
        diagnosis["root_cause_category"] = "unknown"
        diagnosis["confidence"] = "low"
        diagnosis["details"].append("Insufficient evidence to determine root cause")
        diagnosis["human_action"].append("Manually inspect scanner process and logs")
        diagnosis["human_action"].append("Check for OOM kills, segfaults, or external termination")

    return diagnosis


def run() -> dict:
    """Run full diagnostic and return findings."""
    findings = {
        "scanner": "mcp_scanner",
        "diagnostic_timestamp": datetime.utcnow().isoformat() + "Z",
        "heartbeat": {},
        "staleness": {},
        "log_tail": [],
        "process_alive": False,
        "write_service_connectivity": {},
        "loop_analysis": {},
        "root_cause": {},
    }

    # 1. Check write_service connectivity
    findings["write_service_connectivity"] = check_write_service_connectivity()

    # 2. Fetch scanner heartbeat
    findings["heartbeat"] = fetch_scanner_heartbeat()

    # 3. Calculate staleness
    hb_ts = findings["heartbeat"].get("last_heartbeat")
    findings["staleness"] = calculate_staleness(hb_ts)

    # 4. Check process
    findings["process_alive"] = check_process_alive()

    # 5. Read log tail
    findings["log_tail"] = fetch_scanner_log_tail(50)

    # 6. Analyze scanner loop pattern
    findings["loop_analysis"] = analyze_scanner_loop_pattern()

    # 7. Infer root cause
    findings["root_cause"] = infer_root_cause(
        findings["heartbeat"],
        findings["log_tail"],
        findings["loop_analysis"],
        findings["write_service_connectivity"],
    )

    # Record own heartbeat
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": {
                    "target_server_id": "diagnose_scanner_staleness",
                    "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                    "status": "OK",
                    "meta": json.dumps(findings["root_cause"]),
                },
                "wait": True,
            },
            timeout=5,
        )
    except Exception:
        pass

    return findings


def main() -> int:
    """CLI entry point."""
    findings = run()

    print("=" * 60)
    print("  MCP_SCANNER STALENESS DIAGNOSTIC")
    print("=" * 60)
    print(f"Generated : {findings['diagnostic_timestamp']}")
    print()

    # Connectivity
    ws = findings["write_service_connectivity"]
    print("[write_service Connectivity]")
    if ws.get("connected"):
        print(f"  Status   : CONNECTED ({ws.get('latency_ms')}ms latency)")
    else:
        print(f"  Status   : DISCONNECTED ({ws.get('error', 'unknown')})")
    print()

    # Heartbeat
    hb = findings["heartbeat"]
    print("[Heartbeat from service_health]")
    if hb:
        print(f"  Last heartbeat : {hb.get('last_heartbeat', 'N/A')}")
        print(f"  Status         : {hb.get('status', 'N/A')}")
        meta = hb.get("meta", "")
        if meta:
            print(f"  Meta           : {meta[:100]}")
    else:
        print("  No heartbeat record found")
    print()

    # Staleness
    staleness = findings["staleness"]
    print("[Staleness]")
    print(f"  Age     : {staleness.get('stale_human', 'unknown')}")
    print(f"  Seconds : {staleness.get('stale_seconds', -1)}")
    print(f"  Stale   : {staleness.get('is_stale', True)}")
    print()

    # Process
    print(f"[Process] Alive: {findings['process_alive']}")
    print()

    # Loop analysis
    loop = findings["loop_analysis"]
    print("[Scanner Loop Analysis]")
    print(f"  Has run function       : {loop.get('has_run_function')}")
    print(f"  Has heartbeat loop     : {loop.get('has_heartbeat_loop')}")
    print(f"  Has exception handling : {loop.get('has_exception_handling')}")
    print(f"  Has external API calls : {loop.get('has_external_api_calls')}")
    if loop.get("heartbeat_interval_sec"):
        print(f"  Heartbeat interval     : {loop.get('heartbeat_interval_sec')}s")
    if loop.get("loop_sleep_sec"):
        print(f"  Loop sleep             : {loop.get('loop_sleep_sec')}s")
    if loop.get("potential_blocking_calls"):
        print(f"  Blocking calls w/o timeout: {len(loop.get('potential_blocking_calls', []))} found")
    print()

    # Log tail
    print("[Recent Log Lines]")
    for line in findings["log_tail"][-10:]:
        print(f"  {line[:120]}")
    if not findings["log_tail"]:
        print("  (no log file found)")
    print()

    # Root cause
    rc = findings["root_cause"]
    print("[ROOT CAUSE DIAGNOSIS]")
    print(f"  Category  : {rc.get('root_cause_category', 'unknown').upper()}")
    print(f"  Confidence: {rc.get('confidence', 'low')}")
    print()
    print("  Details:")
    for detail in rc.get("details", []):
        print(f"    - {detail}")
    print()
    print("  Recommended Human Action:")
    for action in rc.get("human_action", []):
        print(f"    -> {action}")
    print()
    print("=" * 60)

    # Output JSON for programmatic use
    print("\n[JSON Output]")
    print(json.dumps(findings, indent=2, default=str))

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())