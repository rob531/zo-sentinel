#!/usr/bin/env python3
"""
diagnose_enrichment_pipeline_why_12_rows.py

Diagnostic utility to investigate why mcp_signal_enrichments has only 12 rows
while mcp_signal_scores has 2.3M rows.

Investigates:
1. Recent enrichment write attempts via write_service
2. signal_analyser logs for enrichment invocation errors
3. Verification that enrichment_harness.py was called
"""

import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def get_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def print_header(msg: str) -> None:
    border = "=" * 70
    print(f"\n{border}")
    print(f"  {msg}")
    print(border)


def print_subsection(msg: str) -> None:
    print(f"\n--- {msg} ---")


def query_write_service_enrichment_attempts() -> dict[str, Any]:
    """Query write_service for recent enrichment write attempts."""
    print_subsection("Querying write_service for enrichment write attempts")
    
    results = {
        "status": "unknown",
        "recent_attempts": [],
        "error_count": 0,
        "success_count": 0,
        "raw_output": None,
    }
    
    # Try to query the write_service via CLI or direct DB access
    # Assuming write_service has a CLI or we can query its metrics
    
    try:
        # Check if there's a write_service CLI command
        result = subprocess.run(
            ["write-service", "stats", "--enrichment"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode == 0:
            results["raw_output"] = result.stdout
            results["status"] = "queried_via_cli"
        else:
            results["raw_output"] = result.stderr
            results["status"] = "cli_error"
            
    except FileNotFoundError:
        # CLI not found, try direct database query
        results["status"] = "no_cli_trying_db"
        
        # Check for database connection configuration
        db_path = os.environ.get("WRITE_SERVICE_DB", "/var/lib/write_service/enrichments.db")
        
        if Path(db_path).exists():
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # Query recent enrichment writes
                cursor.execute("""
                    SELECT id, signal_id, created_at, status, error_message
                    FROM enrichment_writes
                    WHERE created_at > datetime('now', '-7 days')
                    ORDER BY created_at DESC
                    LIMIT 50
                """)
                
                rows = cursor.fetchall()
                results["recent_attempts"] = [
                    {
                        "id": r[0],
                        "signal_id": r[1],
                        "created_at": r[2],
                        "status": r[3],
                        "error": r[4],
                    }
                    for r in rows
                ]
                
                # Count by status
                for row in rows:
                    if row[3] == "success":
                        results["success_count"] += 1
                    else:
                        results["error_count"] += 1
                
                results["status"] = "queried_via_db"
                conn.close()
                
            except Exception as e:
                results["status"] = "db_error"
                results["error"] = str(e)
        else:
            results["status"] = "db_not_found"
            results["db_path_checked"] = db_path
    
    return results


def check_signal_analyser_logs() -> dict[str, Any]:
    """Check signal_analyser logs for enrichment invocation errors."""
    print_subsection("Checking signal_analyser logs for enrichment errors")
    
    results = {
        "status": "unknown",
        "log_files_checked": [],
        "enrichment_errors": [],
        "enrichment_invocations": [],
        "total_lines_checked": 0,
    }
    
    log_dirs = [
        "/var/log/signal_analyser",
        "/var/log/mcp/signal_analyser",
        "./logs/signal_analyser",
        "./logs",
    ]
    
    # Also check for configured log path
    if env_log := os.environ.get("SIGNAL_ANALYSER_LOG_DIR"):
        log_dirs.insert(0, env_log)
    
    enrichment_error_patterns = [
        "enrichment",
        "enrich_harness",
        "enrichment_harness",
        "mcp_signal_enrichments",
    ]
    
    for log_dir in log_dirs:
        log_path = Path(log_dir)
        if not log_path.exists():
            continue
            
        results["log_files_checked"].append(str(log_dir))
        
        # Check all log files in the directory
        for log_file in sorted(log_path.glob("*.log")):
            results["log_files_checked"].append(str(log_file))
            
            try:
                # Read last 10000 lines of log file
                with open(log_file, 'r') as f:
                    lines = f.readlines()
                    results["total_lines_checked"] += len(lines)
                
                # Look for enrichment-related entries in recent logs (last 1000 lines)
                recent_lines = lines[-1000:] if len(lines) > 1000 else lines
                
                for line_num, line in enumerate(recent_lines, start=len(lines)-len(recent_lines)):
                    line_lower = line.lower()
                    
                    # Check for enrichment errors
                    if "enrichment" in line_lower and ("error" in line_lower or "failed" in line_lower or "exception" in line_lower):
                        results["enrichment_errors"].append({
                            "file": str(log_file),
                            "line_num": line_num + 1,
                            "timestamp": line.split(" - ")[0] if " - " in line else "unknown",
                            "message": line.strip(),
                        })
                    
                    # Check for enrichment invocations
                    if "enrichment_harness" in line_lower or "Calling enrichment_harness" in line_lower:
                        results["enrichment_invocations"].append({
                            "file": str(log_file),
                            "line_num": line_num + 1,
                            "message": line.strip(),
                        })
                        
            except Exception as e:
                results["errors"] = results.get("errors", []) + [f"Error reading {log_file}: {e}"]
    
    if results["log_files_checked"]:
        results["status"] = "logs_checked"
    else:
        results["status"] = "no_logs_found"
    
    return results


def verify_enrichment_harness_calls() -> dict[str, Any]:
    """Verify if enrichment_harness.py was actually called."""
    print_subsection("Verifying enrichment_harness.py invocation")
    
    results = {
        "status": "unknown",
        "harness_location": None,
        "last_modified": None,
        "invocation_evidence": [],
        "metrics_or_traces": [],
    }
    
    # Find enrichment_harness.py
    possible_paths = [
        "./enrichment_harness.py",
        "./src/enrichment_harness.py",
        "/opt/mcp/services/enrichment_harness.py",
        "/app/enrichment_harness.py",
    ]
    
    harness_path = None
    for path in possible_paths:
        if Path(path).exists():
            harness_path = path
            break
    
    if harness_path:
        results["harness_location"] = harness_path
        results["last_modified"] = datetime.fromtimestamp(
            Path(harness_path).stat().st_mtime
        ).isoformat()
        results["status"] = "found"
        
        # Check for evidence of invocation in metrics/traces
        metrics_files = [
            "./metrics/enrichment_harness_metrics.json",
            "/var/lib/mcp/enrichment_harness_metrics.json",
            "./enrichment_harness_metrics.json",
        ]
        
        for mf in metrics_files:
            if Path(mf).exists():
                try:
                    with open(mf, 'r') as f:
                        metrics = json.load(f)
                    results["metrics_or_traces"].append({
                        "file": mf,
                        "data": metrics,
                    })
                except Exception as e:
                    results["metrics_or_traces"].append({
                        "file": mf,
                        "error": str(e),
                    })
        
        # Check for instrumentation/logs that show harness was called
        try:
            result = subprocess.run(
                ["grep", "-r", "enrichment_harness", "/var/log/", "2>/dev/null"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.stdout:
                results["invocation_evidence"].append({
                    "source": "grep_logs",
                    "matches": result.stdout.splitlines()[:10],  # First 10 matches
                })
        except Exception:
            pass
            
    else:
        results["status"] = "not_found"
        results["searched_paths"] = possible_paths
    
    return results


def check_database_row_counts() -> dict[str, Any]:
    """Check current row counts in both tables."""
    print_subsection("Checking database row counts")
    
    results = {
        "status": "unknown",
        "tables": {},
        "connection_info": None,
    }
    
    # Check for database connection
    db_url = os.environ.get("DATABASE_URL", "")
    
    # Try SQLite first
    sqlite_path = os.environ.get("SQLITE_PATH", "./signal_data.db")
    
    if Path(sqlite_path).exists():
        try:
            import sqlite3
            conn = sqlite3.connect(sqlite_path)
            cursor = conn.cursor()
            
            results["connection_info"] = f"SQLite: {sqlite_path}"
            
            # Count rows in both tables
            for table in ["mcp_signal_enrichments", "mcp_signal_scores"]:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    results["tables"][table] = {
                        "count": count,
                        "status": "found",
                    }
                except sqlite3.OperationalError as e:
                    results["tables"][table] = {
                        "count": None,
                        "status": "table_not_found",
                        "error": str(e),
                    }
            
            # Get some sample data from enrichments to understand structure
            try:
                cursor.execute("""
                    SELECT * FROM mcp_signal_enrichments 
                    ORDER BY created_at DESC LIMIT 5
                """)
                columns = [desc[0] for desc in cursor.description]
                rows = cursor.fetchall()
                results["tables"]["mcp_signal_enrichments"]["sample_data"] = {
                    "columns": columns,
                    "rows": [dict(zip(columns, r)) for r in rows],
                }
            except Exception as e:
                results["tables"]["mcp_signal_enrichments"]["sample_error"] = str(e)
            
            conn.close()
            results["status"] = "queried"
            
        except Exception as e:
            results["status"] = "error"
            results["error"] = str(e)
    else:
        results["status"] = "db_not_found"
        results["checked_path"] = sqlite_path
    
    return results


def check_write_service_health() -> dict[str, Any]:
    """Check write_service health and configuration."""
    print_subsection("Checking write_service health and configuration")
    
    results = {
        "status": "unknown",
        "service_running": None,
        "config": {},
        "errors": [],
    }
    
    # Check if write_service is running
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "write-service"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        results["service_running"] = result.stdout.strip() == "active"
    except Exception as e:
        results["service_running"] = None
        results["errors"].append(f"Could not check service status: {e}")
    
    # Check configuration
    config_paths = [
        "/etc/write_service/config.yaml",
        "./config/write_service.yaml",
        "./write_service_config.json",
    ]
    
    for config_path in config_paths:
        if Path(config_path).exists():
            try:
                with open(config_path, 'r') as f:
                    content = f.read()
                    # Try to parse as YAML or JSON
                    if content.strip().startswith("{"):
                        results["config"] = json.loads(content)
                    else:
                        results["config"]["raw"] = content[:500]  # First 500 chars
                    results["config"]["source"] = config_path
                    results["status"] = "config_found"
                    break
            except Exception as e:
                results["errors"].append(f"Error reading config {config_path}: {e}")
    
    return results


def generate_diagnostic_report() -> dict[str, Any]:
    """Generate complete diagnostic report."""
    print_header("DIAGNOSTIC REPORT - mcp_signal_enrichments Row Count Investigation")
    
    report = {
        "generated_at": get_timestamp(),
        "write_service_attempts": query_write_service_enrichment_attempts(),
        "signal_analyser_logs": check_signal_analyser_logs(),
        "enrichment_harness": verify_enrichment_harness_calls(),
        "database_counts": check_database_row_counts(),
        "write_service_health": check_write_service_health(),
    }
    
    return report


def print_diagnostic_summary(report: dict[str, Any]) -> None:
    """Print a human-readable summary of the diagnostic findings."""
    
    print_header("DIAGNOSTIC SUMMARY")
    
    print_subsection("1. Database Row Counts")
    db_counts = report["database_counts"]
    if db_counts.get("tables"):
        for table, info in db_counts["tables"].items():
            count = info.get("count")
            status = info.get("status", "unknown")
            print(f"   - {table}: {count if count is not None else 'ERROR'} ({status})")
    
    print_subsection("2. Write Service Enrichment Attempts")
    ws_attempts = report["write_service_attempts"]
    print(f"   - Query Status: {ws_attempts.get('status')}")
    print(f"   - Recent Attempts: {len(ws_attempts.get('recent_attempts', []))}")
    print(f"   - Successful Writes: {ws_attempts.get('success_count', 0)}")
    print(f"   - Failed Writes: {ws_attempts.get('error_count', 0)}")
    
    if ws_attempts.get("error"):
        print(f"   - Error: {ws_attempts['error']}")
    
    print_subsection("3. Signal Analyser Logs - Enrichment Errors")
    logs = report["signal_analyser_logs"]
    print(f"   - Log Files Checked: {len(logs.get('log_files_checked', []))}")
    print(f"   - Enrichment Errors Found: {len(logs.get('enrichment_errors', []))}")
    print(f"   - Enrichment Invocations Found: {len(logs.get('enrichment_invocations', []))}")
    
    if logs.get("enrichment_errors"):
        print("\n   Recent Enrichment Errors:")
        for err in logs["enrichment_errors"][:5]:
            print(f"     [{err['file']}:{err['line_num']}] {err['message'][:100]}...")
    
    if logs.get("enrichment_invocations"):
        print("\n   Recent Enrichment Invocations:")
        for inv in logs["enrichment_invocations"][:5]:
            print(f"     [{inv['file']}:{inv['line_num']}] {inv['message'][:100]}...")
    
    print_subsection("4. Enrichment Harness Verification")
    harness = report["enrichment_harness"]
    print(f"   - Harness Status: {harness.get('status')}")
    print(f"   - Harness Location: {harness.get('harness_location')}")
    print(f"   - Last Modified: {harness.get('last_modified')}")
    print(f"   - Invocation Evidence: {len(harness.get('invocation_evidence', []))} sources")
    print(f"   - Metrics/Traces: {len(harness.get('metrics_or_traces', []))} sources")
    
    print_subsection("5. Write Service Health")
    ws_health = report["write_service_health"]
    print(f"   - Service Running: {ws_health.get('service_running')}")
    print(f"   - Config Source: {ws_health.get('config', {}).get('source', 'not found')}")
    
    print_subsection("6. Key Findings")
    
    findings = []
    
    # Analyze the gap
    enrichments_count = report["database_counts"].get("tables", {}).get("mcp_signal_enrichments", {}).get("count", 0)
    scores_count = report["database_counts"].get("tables", {}).get("mcp_signal_scores", {}).get("count", 0)
    
    if enrichments_count and scores_count:
        ratio = scores_count / enrichments_count if enrichments_count > 0 else float('inf')
        findings.append(f"ENRICHMENTS/SCORES RATIO: {enrichments_count:,} enrichments / {scores_count:,} scores = 1:{ratio:,.0f}")
    
    # Check for errors
    error_count = len(report["signal_analyser_logs"].get("enrichment_errors", []))
    if error_count > 0:
        findings.append(f"ERRORS IN LOGS: {error_count} enrichment-related errors found in signal_analyser logs")
    
    # Check for invocation evidence
    invocations = len(report["signal_analyser_logs"].get("enrichment_invocations", []))
    if invocations == 0:
        findings.append("NO INVOCATIONS: enrichment_harness.py appears NOT to be invoked from signal_analyser")
    else:
        findings.append(f"INVOCATIONS FOUND: {invocations} invocation records found")
    
    # Check write service
    ws_errors = report["write_service_attempts"].get("error_count", 0)
    ws_success = report["write_service_attempts"].get("success_count", 0)
    if ws_success == 0 and ws_errors == 0:
        findings.append("WRITE SERVICE: No enrichment write attempts recorded (may indicate pipeline not running)")
    elif ws_errors > ws_success:
        findings.append(f"WRITE SERVICE: More failures ({ws_errors}) than successes ({ws_success})")
    
    for i, finding in enumerate(findings, 1):
        print(f"   {i}. {finding}")
    
    print_header("END OF DIAGNOSTIC REPORT")
    
    return findings


if __name__ == "__main__":
    # Generate the diagnostic report
    report = generate_diagnostic_report()
    
    # Print human-readable summary
    findings = print_diagnostic_summary(report)
    
    # Also output raw JSON for programmatic use
    output_file = f"diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\nRaw report saved to: {output_file}")

EOF
