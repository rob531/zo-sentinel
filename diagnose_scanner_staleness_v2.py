import json
import time
from datetime import datetime, timedelta
from zo_sentinel import service_health, mcp_server_registry

def diagnose_scanner_staleness():
    # Configuration
    STALENESS_THRESHOLD_MINUTES = 45
    RECENT_ACTIVITY_WINDOW_MINUTES = 30

    # Get current timestamp
    now = datetime.utcnow()

    # Query service_health for mcp_scanner entries
    scanner_health = service_health.get_entries(service_name="mcp_scanner")

    # Get scan activity from mcp_server_registry
    scan_activity = mcp_server_registry.get_recent_scans(
        time_window=timedelta(minutes=RECENT_ACTIVITY_WINDOW_MINUTES)
    )

    # Prepare diagnostic results
    results = {
        "timestamp": now.isoformat(),
        "staleness_threshold_minutes": STALENESS_THRESHOLD_MINUTES,
        "scanner_health": [],
        "recent_scan_activity": [],
        "diagnosis": None,
        "recommendation": None
    }

    # Process scanner health entries
    for entry in scanner_health:
        heartbeat_age = (now - entry["last_heartbeat"]).total_seconds() / 60
        results["scanner_health"].append({
            "scanner_id": entry["scanner_id"],
            "heartbeat_age_minutes": heartbeat_age,
            "status": entry["status"],
            "is_stale": heartbeat_age > STALENESS_THRESHOLD_MINUTES
        })

    # Process scan activity
    for scan in scan_activity:
        scan_age = (now - scan["last_scanned"]).total_seconds() / 60
        results["recent_scan_activity"].append({
            "server_id": scan["server_id"],
            "last_scanned": scan["last_scanned"].isoformat(),
            "scan_age_minutes": scan_age,
            "is_recent": scan_age <= RECENT_ACTIVITY_WINDOW_MINUTES
        })

    # Determine diagnosis
    stale_scanners = [s for s in results["scanner_health"] if s["is_stale"]]
    recent_scans = [s for s in results["recent_scan_activity"] if s["is_recent"]]

    if stale_scanners and not recent_scans:
        results["diagnosis"] = "Scanner appears stalled - no recent scan activity detected"
        results["recommendation"] = "Restart scanner service and investigate root cause"
    elif stale_scanners and recent_scans:
        results["diagnosis"] = "Scanner heartbeat stale but still processing work"
        results["recommendation"] = "Monitor closely - may need to restart if staleness persists"
    else:
        results["diagnosis"] = "Scanner operating normally"
        results["recommendation"] = "No action required"

    return results

if __name__ == "__main__":
    diagnostic_results = diagnose_scanner_staleness()
    print(json.dumps(diagnostic_results, indent=2))