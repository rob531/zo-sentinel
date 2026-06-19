#!/usr/bin/env python3
"""
diagnose_mcp_definition_history_empty_gap_v4.py

Diagnostic utility to investigate why mcp_definition_history table is empty (0 rows)
while mcp_server_registry has 1760 rows.

Investigates:
1. Live table schemas and row counts
2. Which daemons should write to mcp_definition_history
3. Whether those daemons are running
4. If the INSERT path is broken or if it's a pipeline gap

Output: Structured JSON to stdout
"""

import json
import os
import subprocess
import sys
import requests
from datetime import datetime, timezone
from typing import Any, Optional

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
TIMEOUT = 10


def get_time_iso() -> str:
    """Return current UTC time in ISO8601 format."""
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[list] = None) -> list:
    """Execute a read query via write_service.
    
    write_service returns {"rows": [...], "count": N} format.
    """
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql, "params": params or []},
            timeout=TIMEOUT
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except requests.exceptions.RequestException as e:
        return [{"_error": str(e)}]
    except json.JSONDecodeError as e:
        return [{"_error": f"JSON decode error: {e}"}]


def ws_query_scalar(sql: str, params: Optional[list] = None) -> Any:
    """Execute a scalar query and return the first column of the first row."""
    rows = ws_query(sql, params)
    if rows and "_error" not in rows[0]:
        return list(rows[0].values())[0] if rows[0] else None
    return None


def get_table_columns(table_name: str) -> list[str]:
    """Query information_schema.columns to get column names for a table."""
    sql = """
    SELECT column_name 
    FROM information_schema.columns 
    WHERE table_name = ?
    ORDER BY ordinal_position
    """
    result = ws_query(sql, [table_name])
    return [row.get("column_name") for row in result if row.get("column_name")]


def check_daemon_processes() -> dict:
    """Check if definition_change daemons are running."""
    daemon_patterns = [
        "definition_change_history_writer",
        "definition_change_monitor",
        "mcp_definition_history_writer"
    ]
    
    results = {}
    for pattern in daemon_patterns:
        try:
            result = subprocess.run(
                ["pgrep", "-f", pattern, "-a"],
                capture_output=True,
                text=True,
                timeout=5
            )
            pids = [p for p in result.stdout.strip().split("\n") if p]
            results[pattern] = {
                "running": len(pids) > 0,
                "pids": pids
            }
        except Exception as e:
            results[pattern] = {"running": False, "error": str(e)}
    
    return results


def check_service_health() -> list[dict]:
    """Check service_health table for daemon heartbeats."""
    sql = """
    SELECT service, status, last_heartbeat, meta
    FROM service_health 
    WHERE service LIKE '%definition%' 
       OR service LIKE '%history%'
       OR service LIKE '%change%'
    ORDER BY last_heartbeat DESC NULLS LAST
    LIMIT 10
    """
    return ws_query(sql)


def check_table_counts() -> dict:
    """Get row counts for both tables."""
    history_count = ws_query_scalar("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    registry_count = ws_query_scalar("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    
    return {
        "mcp_definition_history": history_count if history_count is not None else None,
        "mcp_server_registry": registry_count if registry_count is not None else None
    }


def check_history_schema() -> dict:
    """Get mcp_definition_history table schema."""
    sql = """
    SELECT column_name, data_type, is_nullable, column_default
    FROM information_schema.columns
    WHERE table_name = 'mcp_definition_history'
    ORDER BY ordinal_position
    """
    return {"columns": ws_query(sql), "column_count": len(ws_query(sql))}


def check_registry_has_definition_data() -> dict:
    """Check if mcp_server_registry has entries with definition-related data."""
    # Check for various definition-related columns
    cols = get_table_columns("mcp_server_registry")
    relevant = [c for c in cols if 'def' in c.lower() or 'schema' in c.lower() or 'tool' in c.lower()]
    
    result = {"columns_found": cols, "definition_related_columns": relevant}
    
    # Check if any servers have definition data
    if relevant:
        for col in relevant:
            sql = f"SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE {col} IS NOT NULL"
            cnt = ws_query_scalar(sql)
            result[f"{col}_populated"] = cnt
    else:
        result["no_definition_columns"] = True
        
    return result


def identify_writer_daemons() -> list[dict]:
    """
    Identify which daemon modules are responsible for writing to mcp_definition_history.
    """
    daemons = []
    base = "/home/workspace/zo_sentinel"
    
    files_to_check = [
        ("definition_change_history_writer.py", "definition_change_history_writer"),
        ("definition_change_monitor.py", "definition_change_monitor"),
        ("mcp_definition_history_writer.py", "mcp_definition_history_writer"),
    ]
    
    for filename, service_name in files_to_check:
        path = os.path.join(base, filename)
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read()
            
            # Check if it's a daemon (has run loop and sends heartbeats)
            is_daemon = (
                ("while" in content or "cycle" in content) and
                ("time.sleep" in content or "sleep(" in content)
            )
            has_main = "if __name__ == '__main__'" in content
            sends_heartbeat = "heartbeat" in content.lower() and "requests.post" in content
            writes_to_history = "mcp_definition_history" in content
            
            daemons.append({
                "filename": filename,
                "path": path,
                "service_name": service_name,
                "exists": True,
                "is_daemon": is_daemon,
                "has_main": has_main,
                "sends_heartbeat": sends_heartbeat,
                "writes_to_history": writes_to_history,
                "modified": datetime.fromtimestamp(
                    os.path.getmtime(path), tz=timezone.utc
                ).isoformat()
            })
    
    return daemons


def check_scanner_wiring() -> dict:
    """Check if mcp_scanner has any wiring to definition_history."""
    paths_to_check = [
        "/home/workspace/zo_sentinel/mcp_scanner.py",
        "/home/workspace/zo_sentinel/mcp_scanner_fingerprints_wiring.py"
    ]
    
    for path in paths_to_check:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read()
            return {
                "file": os.path.basename(path),
                "writes_to_definition_history": "mcp_definition_history" in content,
                "calls_history_writer": "definition_change" in content.lower()
            }
    return {"error": "mcp_scanner files not found"}


def check_signal_analyser_wiring() -> dict:
    """Check if signal_analyser has any wiring to definition_history."""
    paths_to_check = [
        "/home/workspace/zo_sentinel/signal_analyser.py",
        "/home/workspace/zo_sentinel/signal_analyser_v2.py",
        "/home/workspace/zo_sentinel/signal_analyser_v3.py"
    ]
    
    for path in paths_to_check:
        if os.path.exists(path):
            with open(path, 'r') as f:
                content = f.read()
            return {
                "file": os.path.basename(path),
                "writes_to_definition_history": "mcp_definition_history" in content,
                "calls_history_writer": "definition_change" in content.lower()
            }
    return {"error": "signal_analyser files not found"}


def diagnose_gap(
    history_count: int,
    registry_count: int,
    daemon_processes: dict,
    daemon_files: list,
    health_records: list
) -> dict:
    """
    Determine root cause of the empty mcp_definition_history table.
    """
    findings = {
        "gap_type": None,
        "root_cause": None,
        "details": {},
        "recommendations": []
    }
    
    # No source data
    if registry_count == 0:
        findings["gap_type"] = "NO_SOURCE_DATA"
        findings["root_cause"] = "mcp_server_registry is empty - no servers to track"
        return findings
    
    # History is empty but registry has data
    if history_count == 0:
        # Check 1: Are daemons running?
        any_running = any(p.get("running", False) for p in daemon_processes.values())
        
        if not any_running:
            # Check 2: Do daemon files exist?
            existing_daemons = [d for d in daemon_files if d["exists"]]
            
            if existing_daemons:
                findings["gap_type"] = "PIPELINE_GAP_DAEMON_NOT_STARTED"
                findings["root_cause"] = (
                    f"Writer daemons exist but are not running. "
                    f"Found {len(existing_daemons)} daemon file(s) but no processes detected."
                )
                findings["details"]["daemon_files"] = existing_daemons
                findings["details"]["running_processes"] = daemon_processes
                findings["recommendations"] = [
                    f"Start the definition_change_history_writer daemon",
                    "Example: python definition_change_history_writer.py --daemon",
                    "The daemon monitors mcp_server_registry for schema changes and writes to mcp_definition_history"
                ]
            else:
                findings["gap_type"] = "PIPELINE_GAP_NO_DAEMON"
                findings["root_cause"] = "No definition_change_history_writer daemon exists - pipeline gap"
                findings["recommendations"] = [
                    "A new daemon module needs to be created to populate mcp_definition_history",
                    "The daemon should monitor mcp_server_registry for definition/schema changes",
                    "On change detection, INSERT into mcp_definition_history"
                ]
        else:
            # Daemon is running - check heartbeat
            if health_records:
                latest = health_records[0]
                last_hb = latest.get("last_heartbeat")
                if last_hb:
                    try:
                        ts = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
                        age = (datetime.now(timezone.utc) - ts).total_seconds()
                        if age > 120:
                            findings["gap_type"] = "DAEMON_STALE"
                            findings["root_cause"] = f"Daemon heartbeat is {age:.0f}s old - may be hung"
                        else:
                            findings["gap_type"] = "INSERT_PATH_BROKEN"
                            findings["root_cause"] = "Daemon is running but INSERT logic appears broken"
                    except:
                        findings["gap_type"] = "INSERT_PATH_BROKEN"
                        findings["root_cause"] = "Daemon running but INSERT path may be broken"
            else:
                findings["gap_type"] = "DAEMON_NO_HEARTBEAT"
                findings["root_cause"] = "Daemon process exists but no heartbeat in service_health"
    
    return findings


def run_diagnostics() -> dict:
    """Run all diagnostics and compile findings."""
    
    findings = {
        "investigation": "mcp_definition_history_empty_gap",
        "timestamp": get_time_iso(),
        "summary": {},
        "table_analysis": {},
        "daemon_analysis": {},
        "wiring_analysis": {},
        "gap_diagnosis": {},
        "root_cause": None,
        "recommendations": []
    }
    
    # Step 1: Get table counts
    counts = check_table_counts()
    findings["summary"] = {
        "mcp_definition_history_rows": counts["mcp_definition_history"],
        "mcp_server_registry_rows": counts["mcp_server_registry"],
        "gap_confirmed": (
            counts["mcp_definition_history"] == 0 and 
            counts["mcp_server_registry"] is not None and
            counts["mcp_server_registry"] > 0
        )
    }
    
    history_count = counts.get("mcp_definition_history") or 0
    registry_count = counts.get("mcp_server_registry") or 0
    
    # Step 2: Analyze table schemas
    history_cols = get_table_columns("mcp_definition_history")
    registry_cols = get_table_columns("mcp_server_registry")
    registry_def_data = check_registry_has_definition_data()
    
    findings["table_analysis"] = {
        "mcp_definition_history": {
            "columns": history_cols,
            "column_count": len(history_cols)
        },
        "mcp_server_registry": {
            "columns": registry_cols,
            "column_count": len(registry_cols),
            "definition_related": registry_def_data
        },
        "history_schema": check_history_schema()
    }
    
    # Step 3: Analyze daemon availability
    daemon_files = identify_writer_daemons()
    processes = check_daemon_processes()
    health = check_service_health()
    
    findings["daemon_analysis"] = {
        "writer_daemons": daemon_files,
        "running_processes": processes,
        "any_daemon_running": any(p.get("running", False) for p in processes.values()),
        "service_health_records": health
    }
    
    # Step 4: Check wiring from other components
    findings["wiring_analysis"] = {
        "mcp_scanner": check_scanner_wiring(),
        "signal_analyser": check_signal_analyser_wiring()
    }
    
    # Step 5: Diagnose the gap
    gap_findings = diagnose_gap(
        history_count,
        registry_count,
        processes,
        daemon_files,
        health
    )
    findings["gap_diagnosis"] = gap_findings
    findings["root_cause"] = gap_findings.get("root_cause")
    findings["recommendations"] = gap_findings.get("recommendations", [])
    
    return findings


def main():
    """Main entry point."""
    print("Investigating mcp_definition_history empty gap...", file=sys.stderr)
    
    findings = run_diagnostics()
    
    # Output as structured JSON to stdout
    print(json.dumps(findings, indent=2))
    
    # Return exit code based on findings
    return 0 if findings["summary"].get("gap_confirmed") is False else 1


if __name__ == "__main__":
    sys.exit(main())
