#!/usr/bin/env python3
"""
diagnose_stale_write_service_heartbeat.py

Diagnostic utility for stale write_service heartbeat (3h52m old).
write_service is protected - do NOT propose rebuild.

Queries service_health table via write_service :8772/query to confirm current
write_service status. Checks if service is responsive despite stale heartbeat.
Logs diagnostic findings. Does NOT write to any tables or attempt to restart services.
"""

import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

# deps: requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
TIMEOUT_SECONDS = 10


def query_service_health() -> list[dict[str, Any]]:
    """Query service_health table for all service status entries."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "sql": "SELECT service_name, status, last_heartbeat, meta FROM service_health ORDER BY last_heartbeat DESC",
                "params": []
            },
            timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except requests.exceptions.Timeout:
        print("[DIAGNOSTIC] ERROR: Timeout querying service_health (10s exceeded)")
        return []
    except requests.exceptions.ConnectionError:
        print("[DIAGNOSTIC] ERROR: Cannot connect to write_service at 127.0.0.1:8772")
        return []
    except Exception as e:
        print(f"[DIAGNOSTIC] ERROR querying service_health: {e}")
        return []


def check_write_service_alive() -> bool:
    """Check if write_service is responsive via a simple health probe."""
    try:
        response = requests.get(
            f"{WRITE_SERVICE_URL}/health",
            timeout=5
        )
        return response.status_code == 200
    except Exception:
        pass
    
    # Fallback: try POST /query with a trivial query
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT 1", "params": []},
            timeout=5
        )
        return response.status_code == 200
    except Exception:
        return False


def parse_heartbeat_timestamp(ts_str: str | None) -> datetime | None:
    """Parse ISO 8601 timestamp string to datetime."""
    if not ts_str:
        return None
    try:
        # Try parsing with timezone
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except Exception:
        pass
    try:
        # Try parsing without timezone (assume UTC)
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def calculate_age_hours(ts: datetime | None) -> float | None:
    """Calculate age of timestamp in hours from now."""
    if not ts:
        return None
    now = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    return delta.total_seconds() / 3600


def format_age(age_hours: float) -> str:
    """Format age in hours to human readable string."""
    if age_hours < 1:
        return f"{age_hours * 60:.1f} minutes"
    elif age_hours < 24:
        return f"{age_hours:.1f} hours"
    else:
        days = age_hours / 24
        return f"{days:.1f} days"


def diagnose_write_service_staleness() -> dict[str, Any]:
    """
    Diagnose write_service heartbeat staleness.
    
    Returns diagnostic findings as a dict.
    """
    findings = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "write_service_reachable": False,
        "write_service_responsive": False,
        "service_health_entries": [],
        "write_service_entry": None,
        "write_service_heartbeat_age_hours": None,
        "is_stale": False,
        "service_responsive_despite_stale": False,
        "summary": "",
        "recommendations": []
    }
    
    # Step 1: Check if write_service is reachable and responsive
    findings["write_service_reachable"] = check_write_service_alive()
    print(f"[DIAGNOSTIC] write_service reachable: {findings['write_service_reachable']}")
    
    # Step 2: Query service_health for all entries
    entries = query_service_health()
    findings["service_health_entries"] = entries
    print(f"[DIAGNOSTIC] Found {len(entries)} service_health entries")
    
    # Step 3: Find write_service entry specifically
    ws_entry = None
    for entry in entries:
        if entry.get("service_name") == "write_service":
            ws_entry = entry
            break
    
    findings["write_service_entry"] = ws_entry
    
    if not ws_entry:
        print("[DIAGNOSTIC] WARNING: No write_service entry found in service_health")
        findings["summary"] = "No write_service entry in service_health table"
        findings["recommendations"].append("write_service may not be registered in service_health")
        findings["recommendations"].append("Check if write_service is running and heartbeating")
        return findings
    
    # Step 4: Analyze write_service heartbeat
    last_heartbeat_str = ws_entry.get("last_heartbeat")
    status = ws_entry.get("status")
    
    print(f"[DIAGNOSTIC] write_service status: {status}")
    print(f"[DIAGNOSTIC] write_service last_heartbeat: {last_heartbeat_str}")
    
    ts = parse_heartbeat_timestamp(last_heartbeat_str)
    if ts:
        age_hours = calculate_age_hours(ts)
        findings["write_service_heartbeat_age_hours"] = age_hours
        age_str = format_age(age_hours) if age_hours else "unknown"
        print(f"[DIAGNOSTIC] write_service heartbeat age: {age_str}")
        
        # Determine staleness (>1 hour is stale)
        if age_hours is not None:
            findings["is_stale"] = age_hours > 1.0
            print(f"[DIAGNOSTIC] write_service heartbeat stale (>1h): {findings['is_stale']}")
    else:
        print("[DIAGNOSTIC] WARNING: Could not parse write_service heartbeat timestamp")
        findings["recommendations"].append("write_service heartbeat timestamp is unparseable")
    
    # Step 5: Check if service is responsive despite stale heartbeat
    if findings["write_service_reachable"]:
        findings["write_service_responsive"] = True
        print("[DIAGNOSTIC] write_service IS responsive via HTTP probe")
        
        if findings["is_stale"]:
            findings["service_responsive_despite_stale"] = True
            print("[DIAGNOSTIC] DIAGNOSIS: write_service is RESPONSIVE but heartbeat is STALE")
            findings["summary"] = "Service responsive but heartbeat is stale (>3h old)"
            findings["recommendations"].append("This is informational only - service may be functioning correctly")
            findings["recommendations"].append("write_service heartbeat mechanism may have stalled but service continues to operate")
            findings["recommendations"].append("No immediate action required unless downstream services report issues")
    else:
        findings["write_service_responsive"] = False
        print("[DIAGNOSTIC] write_service is NOT responsive via HTTP probe")
        
        if findings["is_stale"]:
            findings["summary"] = "write_service heartbeat is stale AND service is not responsive"
            findings["recommendations"].append("write_service may be hung or crashed")
            findings["recommendations"].append("Further investigation required - DO NOT auto-restart without understanding root cause")
        else:
            findings["summary"] = "write_service is not responsive but heartbeat not stale"
            findings["recommendations"].append("Service may be starting up or experiencing temporary issues")
    
    # Step 6: Check meta field for additional context
    meta = ws_entry.get("meta", {})
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    
    if meta:
        print(f"[DIAGNOSTIC] write_service meta: {json.dumps(meta)}")
        
        # Look for error indicators
        if meta.get("error") or meta.get("exception"):
            findings["recommendations"].append(f"Error found in meta: {meta.get('error') or meta.get('exception')}")
    
    return findings


def main() -> int:
    """Main entry point for the diagnostic."""
    print("=" * 70)
    print("write_service Stale Heartbeat Diagnostic")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    print()
    
    findings = diagnose_write_service_staleness()
    
    print()
    print("-" * 70)
    print("DIAGNOSTIC FINDINGS")
    print("-" * 70)
    print(f"write_service reachable:    {findings['write_service_reachable']}")
    print(f"write_service responsive:    {findings['write_service_responsive']}")
    print(f"Heartbeat age (hours):      {findings['write_service_heartbeat_age_hours']}")
    print(f"Is stale (>1h):              {findings['is_stale']}")
    print(f"Responsive despite stale:    {findings['service_responsive_despite_stale']}")
    print()
    print(f"SUMMARY: {findings['summary']}")
    print()
    
    if findings["recommendations"]:
        print("RECOMMENDATIONS:")
        for i, rec in enumerate(findings["recommendations"], 1):
            print(f"  {i}. {rec}")
        print()
    
    # Output JSON for programmatic consumption
    print("-" * 70)
    print("JSON OUTPUT (for programmatic consumption):")
    print(json.dumps(findings, indent=2, default=str))
    print("-" * 70)
    
    # Exit code: 0 if service is responsive (even if stale), 1 if not responsive
    if findings["write_service_responsive"]:
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
