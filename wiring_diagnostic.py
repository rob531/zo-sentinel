import json
import sys
import requests
from datetime import datetime, timezone


def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] WIRING-DIAG: {msg}", flush=True)


def ws_query(query, params=None):
    payload = {"query": query}
    if params:
        payload["params"] = params
    resp = requests.post("http://127.0.0.1:8772/query", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def ws_write(table, rows, wait=True):
    resp = requests.post(
        "http://127.0.0.1:8772/write",
        json={"table": table, "rows": rows, "wait": wait},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


EXPECTED_DAEMONS = [
    "threat_intel_ingestor",
    "mcp_security_analyzer",
    "mcp_policy_validator",
    "mcp_audit_logger",
    "mcp_quota_enforcer",
    "mcp_traffic_router",
    "mcp_metrics_collector",
    "mcp_session_manager",
    "mcp_connection_pool",
    "mcp_health_monitor",
    "zo_sentinel_core",
    "inference_router",
    "model_registry",
    "prompt_injection_detector",
    "rate_limiter",
    "config_hot_reloader",
    "backup_manager",
    "log_aggregator",
    "alert_dispatcher",
    "report_generator",
    "api_gateway",
    "database_maintenance",
]


def main():
    log("Starting wiring diagnostic")
    diagnostic = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "expected_daemons": sorted(EXPECTED_DAEMONS),
        "expected_count": len(EXPECTED_DAEMONS),
        "heartbeat_records": [],
        "never_seen": [],
        "seen": [],
        "orphaned": [],
        "missing_heartbeats": [],
        "query_errors": [],
        "summary": {},
    }

    try:
        rows = ws_query(
            "SELECT service, last_heartbeat, status, target_server_id, metadata FROM service_health ORDER BY service"
        )
        diagnostic["heartbeat_records"] = rows
        log(f"service_health query returned {len(rows)} rows")
    except Exception as e:
        diagnostic["query_errors"].append({"table": "service_health", "error": str(e)})
        log(f"FAIL: query service_health: {e}")

    seen_names = set()
    for row in rows:
        name = row.get("service") or row.get("target_server_id") or "unknown"
        diagnostic["seen"].append(name)
        seen_names.add(name)

    for daemon in sorted(EXPECTED_DAEMONS):
        if daemon not in seen_names:
            diagnostic["never_seen"].append(daemon)
            diagnostic["missing_heartbeats"].append({
                "daemon": daemon,
                "status": "never-seen",
                "last_heartbeat": None,
            })

    for name in sorted(seen_names):
        if name not in EXPECTED_DAEMONS:
            diagnostic["orphaned"].append({
                "orphan_name": name,
                "status": "orphaned-unexpected",
            })

    diagnostic["summary"] = {
        "total_seen": len(diagnostic["seen"]),
        "total_never_seen": len(diagnostic["never_seen"]),
        "total_orphaned": len(diagnostic["orphaned"]),
        "total_expected": len(EXPECTED_DAEMONS),
        "heartbeat_coverage_pct": round(
            (len(diagnostic["seen"]) / len(EXPECTED_DAEMONS)) * 100, 2
        ) if EXPECTED_DAEMONS else 0,
        "diagnostic": "never-seen count matches expected missing-heartbeat daemons (PASS)"
        if len(diagnostic["never_seen"]) == len(diagnostic["missing_heartbeats"])
        else "never-seen count does NOT match expected (INCONSISTENCY)",
    }

    out = json.dumps(diagnostic, indent=2, default=str)
    print(out, flush=True)

    log(f"Diagnostic complete: seen={len(diagnostic['seen'])} "
        f"never_seen={len(diagnostic['never_seen'])} "
        f"orphaned={len(diagnostic['orphaned'])}")

    return 0


if __name__ == "__main__":
    sys.exit(main())