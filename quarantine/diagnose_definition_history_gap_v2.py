#!/usr/bin/env python3
"""
Diagnostic utility to investigate why mcp_definition_history table is empty.
Checks: daemon health, event production, and event flow.
"""

import json
import sys
from datetime import datetime, timedelta
from typing import Any


def query_db(query: str, params: tuple = None) -> list:
    """Execute a query and return results."""
    try:
        import sqlite3
        conn = sqlite3.connect('/var/lib/sentinel/sentinel.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        results = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return results
    except Exception as e:
        return [{"error": str(e)}]


def check_service_health() -> dict:
    """Check definition_change_history_writer daemon health."""
    result = {
        "daemon_status": "unknown",
        "last_heartbeat": None,
        "heartbeat_age_seconds": None,
        "is_healthy": False
    }
    
    query = """
    SELECT service_name, last_heartbeat, status, details
    FROM service_health
    WHERE service_name LIKE '%definition_change_history_writer%'
    ORDER BY last_heartbeat DESC
    LIMIT 1
    """
    
    rows = query_db(query)
    if rows and "error" not in rows[0]:
        if rows[0].get("last_heartbeat"):
            last_hb = rows[0]["last_heartbeat"]
            result["daemon_status"] = rows[0].get("status", "unknown")
            result["last_heartbeat"] = last_hb
            
            # Calculate heartbeat age
            if isinstance(last_hb, str):
                hb_time = datetime.fromisoformat(last_hb)
            else:
                hb_time = last_hb
            age = datetime.now() - hb_time
            result["heartbeat_age_seconds"] = age.total_seconds()
            result["is_healthy"] = age.total_seconds() < 300  # Healthy if < 5 min old
            result["details"] = rows[0].get("details", "")
        else:
            result["daemon_status"] = "no_heartbeat_recorded"
    else:
        result["daemon_status"] = "not_in_health_table"
    
    return result


def check_server_registry() -> dict:
    """Check mcp_server_registry for server activity."""
    result = {
        "total_servers": 0,
        "recent_first_seen": 0,
        "recent_last_updated": 0,
        "servers_by_status": {}
    }
    
    # Get total count
    rows = query_db("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    if rows and "error" not in rows[0]:
        result["total_servers"] = rows[0].get("cnt", 0)
    
    # Get recent first_seen (last 7 days)
    cutoff = datetime.now() - timedelta(days=7)
    rows = query_db(
        "SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE first_seen > ?",
        (cutoff.isoformat(),)
    )
    if rows and "error" not in rows[0]:
        result["recent_first_seen"] = rows[0].get("cnt", 0)
    
    # Get recent last_updated (last 24 hours)
    cutoff = datetime.now() - timedelta(hours=24)
    rows = query_db(
        "SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE last_updated > ?",
        (cutoff.isoformat(),)
    )
    if rows and "error" not in rows[0]:
        result["recent_last_updated"] = rows[0].get("cnt", 0)
    
    # Get servers by status
    rows = query_db("""
        SELECT status, COUNT(*) as cnt 
        FROM mcp_server_registry 
        GROUP BY status
    """)
    if rows and "error" not in rows[0]:
        result["servers_by_status"] = {row["status"]: row["cnt"] for row in rows}
    
    return result


def check_definition_history() -> dict:
    """Check mcp_definition_history table status."""
    result = {
        "total_rows": 0,
        "recent_rows": 0,
        "earliest_record": None,
        "latest_record": None
    }
    
    # Get total count
    rows = query_db("SELECT COUNT(*) as cnt FROM mcp_definition_history")
    if rows and "error" not in rows[0]:
        result["total_rows"] = rows[0].get("cnt", 0)
    
    # Get recent count (last 7 days)
    cutoff = datetime.now() - timedelta(days=7)
    rows = query_db(
        "SELECT COUNT(*) as cnt FROM mcp_definition_history WHERE timestamp > ?",
        (cutoff.isoformat(),)
    )
    if rows and "error" not in rows[0]:
        result["recent_rows"] = rows[0].get("cnt", 0)
    
    # Get earliest and latest
    rows = query_db("SELECT MIN(timestamp) as earliest FROM mcp_definition_history")
    if rows and "error" not in rows[0] and rows[0].get("earliest"):
        result["earliest_record"] = rows[0]["earliest"]
    
    rows = query_db("SELECT MAX(timestamp) as latest FROM mcp_definition_history")
    if rows and "error" not in rows[0] and rows[0].get("latest"):
        result["latest_record"] = rows[0]["latest"]
    
    return result


def check_event_queue() -> dict:
    """Check if events are in the queue/reaching daemon."""
    result = {
        "event_queue_size": 0,
        "recent_events": 0,
        "event_types": []
    }
    
    # Check for event queue table
    rows = query_db("SELECT COUNT(*) as cnt FROM mcp_event_queue WHERE event_type = 'definition_change'")
    if rows and "error" not in rows[0]:
        result["event_queue_size"] = rows[0].get("cnt", 0)
    
    # Check recent events (last hour)
    cutoff = datetime.now() - timedelta(hours=1)
    rows = query_db(
        """SELECT COUNT(*) as cnt FROM mcp_event_queue 
           WHERE event_type = 'definition_change' AND created_at > ?""",
        (cutoff.isoformat(),)
    )
    if rows and "error" not in rows[0]:
        result["recent_events"] = rows[0].get("cnt", 0)
    
    # Get event types
    rows = query_db("SELECT DISTINCT event_type FROM mcp_event_queue")
    if rows and "error" not in rows[0]:
        result["event_types"] = [row["event_type"] for row in rows if row["event_type"]]
    
    return result


def check_daemon_process() -> dict:
    """Check if daemon process is running via system utilities."""
    import subprocess
    result = {
        "process_running": False,
        "pid": None,
        "uptime_seconds": None,
        "process_details": None
    }
    
    try:
        # Check for running process
        proc = subprocess.run(
            ["pgrep", "-f", "definition_change_history_writer"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode == 0 and proc.stdout.strip():
            result["process_running"] = True
            result["pid"] = int(proc.stdout.strip().split()[0])
            
            # Get process details
            proc_detail = subprocess.run(
                ["ps", "-p", str(result["pid"]), "-o", "etime,args"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if proc_detail.returncode == 0:
                result["process_details"] = proc_detail.stdout.strip()
    except Exception as e:
        result["error"] = str(e)
    
    return result


def check_scanner_logs() -> dict:
    """Check scanner for definition_change event production."""
    result = {
        "scanner_logs_accessible": False,
        "recent_definition_events": 0,
        "scanner_errors": []
    }
    
    try:
        import os
        log_paths = [
            "/var/log/sentinel/scanner.log",
            "/var/log/sentinel/mcp_scanner.log",
            "/var/log/sentinel/definition_change_scanner.log"
        ]
        
        for log_path in log_paths:
            if os.path.exists(log_path):
                result["scanner_logs_accessible"] = True
                # Count recent definition_change mentions
                import subprocess
                cutoff = datetime.now() - timedelta(hours=24)
                proc = subprocess.run(
                    ["grep", "-c", "definition_change", log_path],
                    capture_output=True,
                    text=True
                )
                if proc.returncode == 0:
                    result["recent_definition_events"] += int(proc.stdout.strip() or 0)
    except Exception as e:
        result["scanner_errors"] = [str(e)]
    
    return result


def check_writer_logs() -> dict:
    """Check writer daemon logs for event reception."""
    result = {
        "writer_logs_accessible": False,
        "events_received_count": 0,
        "errors": []
    }
    
    try:
        import os
        import subprocess
        log_paths = [
            "/var/log/sentinel/definition_change_history_writer.log",
            "/var/log/sentinel/writer.log",
            "/var/log/sentinel/history_writer.log"
        ]
        
        for log_path in log_paths:
            if os.path.exists(log_path):
                result["writer_logs_accessible"] = True
                cutoff = datetime.now() - timedelta(hours=24)
                proc = subprocess.run(
                    ["grep", "-c", "received.*definition_change", log_path],
                    capture_output=True,
                    text=True
                )
                if proc.returncode == 0:
                    result["events_received_count"] += int(proc.stdout.strip() or 0)
                
                # Check for errors
                proc = subprocess.run(
                    ["grep", "-i", "error", log_path],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if proc.returncode == 0 and proc.stdout:
                    lines = proc.stdout.strip().split('\n')[-5:]  # Last 5 error lines
                    result["errors"] = lines
    except Exception as e:
        result["errors"] = [str(e)]
    
    return result


def main():
    findings = {
        "diagnostic_timestamp": datetime.now().isoformat(),
        "investigation_target": "mcp_definition_history_empty",
        "expected_servers": 1754,
        "findings": {}
    }
    
    # Run all diagnostic checks
    findings["findings"]["daemon_health"] = check_service_health()
    findings["findings"]["server_registry"] = check_server_registry()
    findings["findings"]["definition_history"] = check_definition_history()
    findings["findings"]["event_queue"] = check_event_queue()
    findings["findings"]["daemon_process"] = check_daemon_process()
    findings["findings"]["scanner_logs"] = check_scanner_logs()
    findings["findings"]["writer_logs"] = check_writer_logs()
    
    # Summary analysis
    findings["summary"] = {
        "gap_confirmed": findings["findings"]["server_registry"]["total_servers"] > 0 and 
                         findings["findings"]["definition_history"]["total_rows"] == 0,
        "daemon_running": findings["findings"]["daemon_process"]["process_running"],
        "heartbeat_healthy": findings["findings"]["daemon_health"]["is_healthy"],
        "events_in_queue": findings["findings"]["event_queue"]["event_queue_size"] > 0,
        "scanner_producing": findings["findings"]["scanner_logs"]["recent_definition_events"] > 0,
        "writer_receiving": findings["findings"]["writer_logs"]["events_received_count"] > 0
    }
    
    # Diagnosis
    if not findings["summary"]["daemon_running"]:
        findings["diagnosis"] = "DAEMON_NOT_RUNNING - definition_change_history_writer process is not running"
    elif not findings["summary"]["heartbeat_healthy"]:
        findings["diagnosis"] = "DAEMON_UNHEALTHY - daemon running but heartbeat is stale"
    elif not findings["summary"]["scanner_producing"]:
        findings["diagnosis"] = "SCANNER_NOT_PRODUCING - mcp_scanner is not generating definition_change events"
    elif not findings["summary"]["events_in_queue"]:
        findings["diagnosis"] = "EVENT_TRANSPORT_BROKEN - scanner produces events but they don't reach queue"
    elif not findings["summary"]["writer_receiving"]:
        findings["diagnosis"] = "WRITER_NOT_RECEIVING - events in queue but writer not processing them"
    else:
        findings["diagnosis"] = "UNKNOWN_GAP - all checks pass but history table is empty; possible DB write issue"
    
    print(json.dumps(findings, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())