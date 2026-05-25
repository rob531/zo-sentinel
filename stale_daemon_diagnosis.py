#!/usr/bin/env python3
"""
ZO-SENTINEL: Stale Daemon Diagnosis Module
Analyzes three stale daemons: write_service (55m), self_diagnostics (25m), rug_pull_monitor (592h56m)
"""

import requests
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger('stale_daemon_diagnosis')

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "stale_daemon_diagnosis"
SERVICE_PORT = 8774

STALE_DAEMONS = [
    {"name": "write_service", "expected_max_stale_minutes": 10},
    {"name": "self_diagnostics", "expected_max_stale_minutes": 15},
    {"name": "rug_pull_monitor", "expected_max_stale_minutes": 60},
]


def ws_query(query: str, params: Optional[Dict] = None) -> Optional[List[Dict]]:
    """Query write_service API."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"query": query, "params": params},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except Exception as e:
        log.error(f"WS query failed: {e}")
        return None


def ws_write(table: str, rows: Dict) -> bool:
    """Write to write_service API."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=15
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"WS write failed: {e}")
        return False


def ws_execute(query: str, params: Optional[Dict] = None) -> bool:
    """Execute on write_service API."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/execute",
            json={"query": query, "params": params},
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        log.error(f"WS execute failed: {e}")
        return False


def check_write_service_health() -> Dict[str, Any]:
    """Check if write_service on port 8772 is responsive."""
    health = {
        "responsive": False,
        "latency_ms": None,
        "error": None,
        "status_code": None
    }
    try:
        start = datetime.now()
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"query": "SELECT 1 as test", "params": None},
            timeout=5
        )
        latency = (datetime.now() - start).total_seconds() * 1000
        health["latency_ms"] = round(latency, 2)
        health["status_code"] = response.status_code
        health["responsive"] = response.status_code == 200
    except Exception as e:
        health["error"] = str(e)
    return health


def get_daemon_health_status() -> Dict[str, Dict]:
    """Query service_health table for all daemon statuses."""
    query = """
    SELECT 
        service,
        last_heartbeat,
        status,
        created_at,
        updated_at,
        metadata
    FROM service_health
    WHERE service IN ('write_service', 'self_diagnostics', 'rug_pull_monitor')
    ORDER BY service
    """
    results = ws_query(query)
    
    if results is None:
        log.error("Failed to query service_health table")
        return {}
    
    daemon_status = {}
    for row in results:
        service_name = row.get("service")
        last_heartbeat_str = row.get("last_heartbeat")
        status = row.get("status", "unknown")
        
        last_heartbeat = None
        if last_heartbeat_str:
            try:
                last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace('Z', '+00:00'))
            except:
                pass
        
        daemon_status[service_name] = {
            "service": service_name,
            "last_heartbeat": last_heartbeat,
            "last_heartbeat_str": last_heartbeat_str,
            "status": status,
            "metadata": row.get("metadata")
        }
    
    return daemon_status


def calculate_staleness(daemon_status: Dict) -> Dict:
    """Calculate how stale a daemon is."""
    if not daemon_status or not daemon_status.get("last_heartbeat"):
        return {
            "is_stale": True,
            "stale_minutes": None,
            "stale_hours": None,
            "severity": "critical"
        }
    
    last_hb = daemon_status["last_heartbeat"]
    now = datetime.now(last_hb.tzinfo) if last_hb.tzinfo else datetime.now()
    stale_delta = now - last_hb
    stale_minutes = stale_delta.total_seconds() / 60
    stale_hours = stale_minutes / 60
    
    for daemon_config in STALE_DAEMONS:
        if daemon_config["name"] == daemon_status["service"]:
            max_stale = daemon_config["expected_max_stale_minutes"]
            is_stale = stale_minutes > max_stale
            severity = "critical" if stale_minutes > max_stale * 10 else "high" if stale_minutes > max_stale * 3 else "medium" if stale_minutes > max_stale else "normal"
            return {
                "is_stale": is_stale,
                "stale_minutes": round(stale_minutes, 2),
                "stale_hours": round(stale_hours, 2),
                "max_expected_minutes": max_stale,
                "severity": severity
            }
    
    return {
        "is_stale": False,
        "stale_minutes": round(stale_minutes, 2),
        "stale_hours": round(stale_hours, 2),
        "severity": "unknown"
    }


def diagnose_rug_pull_monitor(daemon_status: Dict, ws_health: Dict) -> Dict:
    """Specialized diagnosis for rug_pull_monitor (592h stale)."""
    diagnosis = {
        "service": "rug_pull_monitor",
        "possible_causes": [],
        "recommendations": []
    }
    
    stale_info = calculate_staleness(daemon_status)
    diagnosis["stale_hours"] = stale_info.get("stale_hours", 0)
    
    if stale_info.get("stale_hours", 0) > 500:
        diagnosis["possible_causes"].append({
            "cause": "CRASH_LOOP_BACKOFF",
            "probability": "high",
            "reasoning": "Stale for 592h (~25 days) suggests process crashed and has not restarted"
        })
    
    if not ws_health.get("responsive"):
        diagnosis["possible_causes"].append({
            "cause": "DEPENDENCY_UNAVAILABLE",
            "probability": "medium",
            "reasoning": "write_service may be having issues, affecting rug_pull_monitor's ability to report"
        })
    
    if daemon_status.get("status") == "stopped":
        diagnosis["possible_causes"].append({
            "cause": "SERVICE_STOPPED",
            "probability": "high",
            "reasoning": "Service explicitly marked as stopped in health table"
        })
    
    if daemon_status.get("status") == "error":
        diagnosis["possible_causes"].append({
            "cause": "ERROR_STATE",
            "probability": "high",
            "reasoning": "Service in error state - check logs for exceptions"
        })
    
    diagnosis["possible_causes"].append({
        "cause": "MISSED_HEARTBEATS_DUE_TO_EXECUTION_BLOCK",
        "probability": "medium",
        "reasoning": "Long-running operations may block heartbeat emission"
    })
    
    diagnosis["recommendations"] = [
        "Check rug_pull_monitor process status with systemctl/service manager",
        "Review logs for rug_pull_monitor from past 592 hours",
        "Check if process is running: ps aux | grep rug_pull",
        "Attempt to restart service",
        "Review database for any pending rug_pull operations",
        "Check for OOM kills or resource exhaustion"
    ]
    
    return diagnosis


def generate_diagnosis_report() -> Dict:
    """Generate comprehensive diagnosis report."""
    report = {
        "generated_at": datetime.now().isoformat(),
        "write_service_health": None,
        "daemon_analyses": [],
        "critical_findings": []
    }
    
    log.info("Checking write_service responsiveness on port 8772...")
    ws_health = check_write_service_health()
    report["write_service_health"] = ws_health
    
    log.info("Querying service_health for daemon statuses...")
    daemon_statuses = get_daemon_health_status()
    
    log.info("Analyzing each daemon...")
    for daemon_config in STALE_DAEMONS:
        daemon_name = daemon_config["name"]
        status = daemon_statuses.get(daemon_name, {})
        stale_info = calculate_staleness(status)
        
        analysis = {
            "service": daemon_name,
            "last_heartbeat": status.get("last_heartbeat_str"),
            "status": status.get("status"),
            "is_stale": stale_info["is_stale"],
            "stale_minutes": stale_info.get("stale_minutes"),
            "stale_hours": stale_info.get("stale_hours"),
            "severity": stale_info.get("severity"),
            "expected_max_minutes": stale_info.get("max_expected_minutes")
        }
        
        if daemon_name == "rug_pull_monitor" and stale_info.get("stale_hours", 0) > 24:
            diagnosis = diagnose_rug_pull_monitor(status, ws_health)
            analysis["rug_pull_specific_diagnosis"] = diagnosis
            report["critical_findings"].append(diagnosis)
        
        if stale_info["is_stale"]:
            report["critical_findings"].append({
                "type": "stale_daemon",
                "service": daemon_name,
                "stale_hours": stale_info.get("stale_hours"),
                "severity": stale_info.get("severity")
            })
        
        report["daemon_analyses"].append(analysis)
        log.info(f"  {daemon_name}: stale={stale_info['is_stale']}, {stale_info.get('stale_hours', 0):.2f}h, severity={stale_info.get('severity')}")
    
    return report


def send_heartbeat() -> bool:
    """Send heartbeat to write_service."""
    return ws_write("service_health", {
        "service": SERVICE_NAME,
        "status": "running",
        "last_heartbeat": datetime.now().isoformat()
    })


def save_diagnosis_report(report: Dict) -> bool:
    """Save diagnosis report to write_service."""
    return ws_write("stale_diagnosis_reports", {
        "service": SERVICE_NAME,
        "generated_at": report["generated_at"],
        "write_service_responsive": report["write_service_health"].get("responsive", False),
        "daemons_analyzed": len(report["daemon_analyses"]),
        "critical_findings": len(report["critical_findings"]),
        "report_data": json.dumps(report)
    })


def run():
    """Main diagnostic run loop."""
    log.info("Starting stale daemon diagnosis...")
    log.info(f"Checking: {[d['name'] for d in STALE_DAEMONS]}")
    
    report = generate_diagnosis_report()
    
    log.info("Saving diagnosis report...")
    save_diagnosis_report(report)
    
    log.info("Sending heartbeat...")
    send_heartbeat()
    
    log.info("Stale daemon diagnosis complete")
    log.info(f"Critical findings: {len(report['critical_findings'])}")
    
    for finding in report["critical_findings"]:
        log.warning(f"Finding: {finding.get('type', 'unknown') or finding.get('service', 'unknown')}")
    
    return report


if __name__ == "__main__":
    run()