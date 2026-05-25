#!/usr/bin/env python3
"""
performance_monitor.py -- ZO-SENTINEL performance monitoring daemon.
Monitors health endpoints of all services and tracks latency metrics.
"""
import requests
import logging
import time
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, Any, List, Optional

log = logging.getLogger(__name__)

SERVICE_NAME = "performance_monitor"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 300
PERFORMANCE_LOG_PATH = "PERFORMANCE_LOG.md"
DEGRADATION_THRESHOLD_MS = 500
READINGS_PER_CHECK = 3

SERVICES = {
    "write_service": "http://127.0.0.1:8772/health",
    "registry_api": "http://127.0.0.1:8781/health",
    "approval_workflow": "http://127.0.0.1:8780/health",
    "search_api": "http://127.0.0.1:8782/health",
}

perf_metrics: Dict[str, List[float]] = defaultdict(list)
recent_events: List[Dict[str, Any]] = []


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service."""
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", []) if isinstance(data, dict) else data
    except Exception as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    """Write to write_service using 'rows' field."""
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={table: rows}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Write failed: {e}")
        return False


def send_heartbeat() -> bool:
    """Send heartbeat to write_service."""
    try:
        return ws_write("service_health", {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.utcnow().isoformat()
        })
    except Exception:
        return False


def measure_latency(url: str) -> tuple[Optional[float], bool]:
    """Measure response time for a health endpoint. Returns (latency_ms, reachable)."""
    try:
        start = time.time()
        resp = requests.get(url, timeout=10)
        latency_ms = (time.time() - start) * 1000
        return latency_ms, resp.status_code == 200
    except requests.exceptions.Timeout:
        return None, False
    except Exception:
        return None, False


def check_service_health(service_name: str, url: str) -> Dict[str, Any]:
    """Check service health with multiple readings."""
    readings = []
    reachable = True
    
    for _ in range(READINGS_PER_CHECK):
        latency, is_up = measure_latency(url)
        if latency is not None:
            readings.append(latency)
        if not is_up:
            reachable = False
        time.sleep(0.5)
    
    avg_latency = sum(readings) / len(readings) if readings else None
    
    return {
        "service": service_name,
        "latency_ms": avg_latency,
        "reachable": reachable,
        "readings_count": len(readings),
        "timestamp": datetime.utcnow().isoformat()
    }


def record_perf_metrics(result: Dict[str, Any]) -> None:
    """Record performance metrics for rolling 24h window."""
    service = result["service"]
    latency = result["latency_ms"]
    
    if latency is not None:
        perf_metrics[service].append({
            "latency": latency,
            "timestamp": result["timestamp"]
        })
    
    cutoff = datetime.utcnow() - timedelta(hours=24)
    perf_metrics[service] = [
        m for m in perf_metrics[service]
        if datetime.fromisoformat(m["timestamp"]) > cutoff
    ]


def check_performance_degradation(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Check if service is degraded (>500ms avg over readings)."""
    service = result["service"]
    latency = result["latency_ms"]
    
    if latency is not None and latency > DEGRADATION_THRESHOLD_MS:
        return {
            "service": service,
            "severity": "WARNING",
            "event_type": "performance_degradation",
            "latency_ms": round(latency, 2),
            "threshold_ms": DEGRADATION_THRESHOLD_MS,
            "message": f"Service {service} latency {latency:.2f}ms exceeds threshold {DEGRADATION_THRESHOLD_MS}ms"
        }
    return None


def check_unreachable(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Check if service is unreachable."""
    if not result["reachable"]:
        return {
            "service": result["service"],
            "severity": "CRITICAL",
            "event_type": "service_unreachable",
            "latency_ms": None,
            "message": f"Service {result['service']} is unreachable"
        }
    return None


def write_event(event: Dict[str, Any]) -> bool:
    """Write performance event to mesh_events table."""
    return ws_write("mesh_events", {
        "server_id": event["service"],
        "event_type": event["event_type"],
        "severity": event["severity"],
        "details": event["message"],
        "timestamp": datetime.utcnow().isoformat()
    })


def generate_stats_summary() -> Dict[str, Any]:
    """Generate summary stats for each service over 24h window."""
    summary = {}
    
    for service, metrics in perf_metrics.items():
        if metrics:
            latencies = [m["latency"] for m in metrics]
            summary[service] = {
                "count": len(latencies),
                "avg_ms": round(sum(latencies) / len(latencies), 2),
                "min_ms": round(min(latencies), 2),
                "max_ms": round(max(latencies), 2),
                "degraded_count": sum(1 for l in latencies if l > DEGRADATION_THRESHOLD_MS)
            }
    
    return summary


def write_performance_log() -> None:
    """Write rolling 24h performance stats to PERFORMANCE_LOG.md."""
    summary = generate_stats_summary()
    
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    lines = [
        f"# ZO-SENTINEL Performance Log",
        f"",
        f"**Last Updated:** {now}",
        f"",
        f"## 24-Hour Summary",
        f"",
    ]
    
    if summary:
        lines.append("| Service | Readings | Avg (ms) | Min (ms) | Max (ms) | Degraded |")
        lines.append("|---------|----------|----------|----------|----------|----------|")
        
        for service, stats in sorted(summary.items()):
            lines.append(
                f"| {service} | {stats['count']} | {stats['avg_ms']} | "
                f"{stats['min_ms']} | {stats['max_ms']} | {stats['degraded_count']} |"
            )
        
        lines.append("")
        lines.append("## Thresholds")
        lines.append(f"- **Degradation Threshold:** {DEGRADATION_THRESHOLD_MS}ms")
        lines.append(f"- **Readings per Check:** {READINGS_PER_CHECK}")
        lines.append("")
        
        critical_events = [e for e in recent_events if e.get("severity") == "CRITICAL"]
        warning_events = [e for e in recent_events if e.get("severity") == "WARNING"]
        
        if critical_events:
            lines.append("## Critical Events (Last 24h)")
            for e in critical_events[-10:]:
                lines.append(f"- [{e['timestamp']}] {e['service']}: {e['message']}")
            lines.append("")
        
        if warning_events:
            lines.append("## Warning Events (Last 24h)")
            for e in warning_events[-10:]:
                lines.append(f"- [{e['timestamp']}] {e['service']}: {e['message']}")
            lines.append("")
    else:
        lines.append("No performance data available.")
        lines.append("")
    
    lines.append(f"*Generated by {SERVICE_NAME}*")
    
    try:
        with open(PERFORMANCE_LOG_PATH, "w") as f:
            f.write("\n".join(lines))
        log.info(f"Performance log written to {PERFORMANCE_LOG_PATH}")
    except Exception as e:
        log.error(f"Failed to write performance log: {e}")


def cleanup_old_events() -> None:
    """Remove events older than 24 hours."""
    global recent_events
    cutoff = datetime.utcnow() - timedelta(hours=24)
    recent_events = [
        e for e in recent_events
        if datetime.fromisoformat(e["timestamp"]) > cutoff
    ]


def cycle() -> None:
    """Run one monitoring cycle."""
    log.info("Running performance monitoring cycle...")
    
    for service_name, health_url in SERVICES.items():
        result = check_service_health(service_name, health_url)
        
        log.info(
            f"Service {service_name}: latency={result['latency_ms']}ms, "
            f"reachable={result['reachable']}"
        )
        
        record_perf_metrics(result)
        
        degradation = check_performance_degradation(result)
        if degradation:
            log.warning(degradation["message"])
            write_event(degradation)
            recent_events.append({
                **degradation,
                "timestamp": datetime.utcnow().isoformat()
            })
        
        unreachable = check_unreachable(result)
        if unreachable:
            log.error(unreachable["message"])
            write_event(unreachable)
            recent_events.append({
                **unreachable,
                "timestamp": datetime.utcnow().isoformat()
            })
    
    cleanup_old_events()
    write_performance_log()


def heartbeat_loop() -> None:
    """Heartbeat loop."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run() -> None:
    """Main run function."""
    log.info(f"Starting {SERVICE_NAME}...")
    
    cycle()
    
    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    
    log.info(f"{SERVICE_NAME} running. Monitoring interval: {CYCLE_INTERVAL}s")
    
    while True:
        time.sleep(CYCLE_INTERVAL)
        cycle()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    run()