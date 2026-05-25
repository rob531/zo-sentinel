#!/usr/bin/env python3
"""
diagnose_signal_analyser_staleness.py
Diagnostic script to check signal_analyser heartbeat staleness.
Does NOT rebuild or restart services.
"""

import json
import time
from datetime import datetime, timezone
import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
STALENESS_THRESHOLD = 7200  # seconds


def query_service(sql: str) -> dict:
    """Query write_service and return results."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"sql": sql},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "rows": []}


def calculate_heartbeat_age(last_heartbeat: str) -> float:
    """Calculate age of heartbeat in seconds."""
    try:
        dt = datetime.fromisoformat(last_heartbeat.replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age
    except Exception:
        return -1


def get_last_signal_processed() -> dict:
    """Get the most recent signal score timestamp."""
    sql = """
    SELECT scored_at 
    FROM mcp_signal_scores 
    ORDER BY scored_at DESC 
    LIMIT 1
    """
    result = query_service(sql)
    if result.get("rows"):
        return result["rows"][0]
    return {}


def check_service_health() -> dict:
    """Check signal_analyser heartbeat status."""
    sql = """
    SELECT service, last_heartbeat 
    FROM service_health 
    WHERE service = 'signal_analyser'
    """
    result = query_service(sql)
    if result.get("rows"):
        return result["rows"][0]
    return {}


def check_recent_logs() -> dict:
    """Check for recent errors in logs."""
    log_checks = {
        "import_errors": [],
        "crash_indicators": []
    }
    
    # Check common log locations
    log_paths = [
        "/tmp/signal_analyser.log",
        "/tmp/signal_analyser.err",
        "/var/log/signal_analyser.log"
    ]
    
    for log_path in log_paths:
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                # Get last 50 lines for recent activity
                recent = lines[-50:] if len(lines) > 50 else lines
                for line in recent:
                    lower = line.lower()
                    if "importerror" in lower or "import error" in lower or "modulenotfounderror" in lower:
                        log_checks["import_errors"].append(line.strip())
                    if "traceback" in lower or "crashed" in lower or "fatal" in lower:
                        log_checks["crash_indicators"].append(line.strip())
        except FileNotFoundError:
            pass
        except Exception:
            pass
    
    return log_checks


def check_write_service_connectivity() -> dict:
    """Verify write_service is reachable."""
    try:
        start = time.time()
        resp = requests.get("http://127.0.0.1:8772/health", timeout=5)
        latency_ms = (time.time() - start) * 1000
        return {
            "reachable": True,
            "status_code": resp.status_code,
            "latency_ms": round(latency_ms, 2)
        }
    except Exception as e:
        return {
            "reachable": False,
            "error": str(e)
        }


def main():
    """Run diagnostic and output JSON report."""
    report = {
        "diagnostic_timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "signal_analyser",
        "staleness_threshold_sec": STALENESS_THRESHOLD,
        "heartbeat": {},
        "signal_scores_last_processed": {},
        "is_stale": False,
        "heartbeat_age_sec": None,
        "suspected_causes": [],
        "connectivity": {},
        "log_analysis": {},
        "recommendations": []
    }
    
    # Check write_service connectivity first
    report["connectivity"] = check_write_service_connectivity()
    
    if not report["connectivity"].get("reachable"):
        report["suspected_causes"].append("write_service unreachable")
        report["recommendations"].append("Verify write_service is running on port 8772")
        print(json.dumps(report, indent=2))
        return
    
    # Check heartbeat
    health = check_service_health()
    report["heartbeat"] = health
    
    if health.get("last_heartbeat"):
        age = calculate_heartbeat_age(health["last_heartbeat"])
        report["heartbeat_age_sec"] = round(age, 2)
        report["is_stale"] = age > STALENESS_THRESHOLD
        
        if report["is_stale"]:
            report["suspected_causes"].append(f"Heartbeat stale: {round(age, 0)}s old (threshold: {STALENESS_THRESHOLD}s)")
            report["recommendations"].append("signal_analyser loop may have crashed - check logs for traceback")
            report["recommendations"].append("Verify signal_analyser.py imports are valid")
    else:
        report["suspected_causes"].append("No heartbeat record found in service_health")
        report["recommendations"].append("signal_analyser may never have started successfully")
    
    # Check last processed timestamp
    last_processed = get_last_signal_processed()
    report["signal_scores_last_processed"] = last_processed
    
    # Analyze logs
    log_analysis = check_recent_logs()
    report["log_analysis"] = log_analysis
    
    if log_analysis.get("import_errors"):
        report["suspected_causes"].append("Import errors detected in logs")
        report["recommendations"].append("Fix import statements in signal_analyser.py")
    
    if log_analysis.get("crash_indicators"):
        report["suspected_causes"].append("Crash indicators found in logs")
        report["recommendations"].append("Review stack trace in signal_analyser logs")
    
    # Generate summary recommendation
    if not report["is_stale"] and not report["suspected_causes"]:
        report["recommendations"].append("Service appears healthy - no action required")
    elif report["is_stale"] and not log_analysis["import_errors"] and not log_analysis["crash_indicators"]:
        report["recommendations"].append("Stale heartbeat but no log errors - possible infinite loop or sleep deadlock")
        report["recommendations"].append("Enable debug logging to trace execution flow")
    
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()