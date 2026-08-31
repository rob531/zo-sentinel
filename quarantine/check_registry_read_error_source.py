#!/usr/bin/env python3
# deps: requests
"""
Investigate 'Registry read error' in current build state.

Queries write_service for recent errors in:
  - service_health (status='error' or message containing 'registry')
  - audit_log (events mentioning 'registry read')
  - build_provenance (recent failed builds mentioning registry)

Identifies which service emitted the error and whether it is blocking
pipeline progress. Reports findings as structured JSON to stdout.

Diagnostic-only module: no DB writes, no daemon registration.
"""
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_TIMEOUT = 10


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service. Returns rows list or raises on error."""
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        timeout=QUERY_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


# ------------------------------------------------------------------
# Probe 1: write_service basic connectivity
# ------------------------------------------------------------------
def probe_connectivity() -> dict[str, Any]:
    started = time.monotonic()
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": "SELECT 1 AS alive"},
            timeout=QUERY_TIMEOUT,
        )
        return {
            "reachable": True,
            "status_code": resp.status_code,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "ok": resp.status_code == 200,
            "error": None,
        }
    except Exception as e:
        return {
            "reachable": False,
            "status_code": None,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "ok": False,
            "error": str(e),
        }


# ------------------------------------------------------------------
# Probe 2: service_health errors mentioning 'registry' or 'read error'
# ------------------------------------------------------------------
def probe_service_health_errors(limit: int = 30) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table_exists": False,
        "registry_error_rows": [],
        "all_recent_errors": [],
        "error": None,
    }
    try:
        # General errors from service_health
        all_err = ws_query(f"""
            SELECT service, status, ts, meta
            FROM service_health
            WHERE status = 'error'
               OR meta::VARCHAR ILIKE '%registry%'
               OR meta::VARCHAR ILIKE '%read error%'
            ORDER BY ts DESC
            LIMIT {limit}
        """)
        result["table_exists"] = True
        result["registry_error_rows"] = [
            r for r in all_err
            if "registry" in str(r.get("meta", "")).lower()
            or "read error" in str(r.get("meta", "")).lower()
        ]
        result["all_recent_errors"] = all_err

    except Exception as e:
        result["error"] = str(e)
    return result


# ------------------------------------------------------------------
# Probe 3: audit_log registry read errors
# ------------------------------------------------------------------
def probe_audit_log(limit: int = 20) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table_exists": False,
        "registry_read_error_rows": [],
        "error": None,
    }
    try:
        rows = ws_query(f"""
            SELECT event_id, event_type, action, outcome,
                   details_json, timestamp, actor, target_server_id
            FROM audit_log
            WHERE event_type ILIKE '%registry%'
               OR action ILIKE '%registry%'
               OR details_json ILIKE '%registry read%'
               OR details_json ILIKE '%registry_read%'
            ORDER BY timestamp DESC
            LIMIT {limit}
        """)
        result["table_exists"] = True
        result["registry_read_error_rows"] = [
            r for r in rows
            if "read" in str(r.get("details_json", "")).lower()
            or "read" in str(r.get("action", "")).lower()
        ]
    except Exception as e:
        result["error"] = str(e)
    return result


# ------------------------------------------------------------------
# Probe 4: build_provenance recent failures referencing registry
# ------------------------------------------------------------------
def probe_build_provenance(limit: int = 20) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table_exists": False,
        "recent_failures": [],
        "registry_related_failures": [],
        "error": None,
    }
    try:
        rows = ws_query(f"""
            SELECT build_id, directive_type, complexity, engine,
                   model, smoke_result, success, error, built_at
            FROM build_provenance
            ORDER BY built_at DESC
            LIMIT {limit}
        """)
        result["table_exists"] = True
        result["recent_failures"] = rows

        result["registry_related_failures"] = [
            r for r in rows
            if not r.get("success", True)
            and any(
                k for k in [r.get("error", ""), r.get("directive_type", "")]
                if k and "registry" in k.lower()
            )
        ]
    except Exception as e:
        result["error"] = str(e)
    return result


# ------------------------------------------------------------------
# Probe 5: mcp_server_registry integrity
# ------------------------------------------------------------------
def probe_registry_table(limit: int = 10) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table_exists": False,
        "columns": [],
        "row_count": None,
        "sample": [],
        "error": None,
    }
    try:
        cols = ws_query("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'mcp_server_registry'
            ORDER BY ordinal_position
        """)
        result["table_exists"] = len(cols) > 0
        result["columns"] = cols

        if result["table_exists"]:
            count_rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_server_registry")
            result["row_count"] = count_rows[0]["cnt"] if count_rows else 0

            sample = ws_query(f"""
                SELECT server_id, name, enabled, description
                FROM mcp_server_registry
                LIMIT {limit}
            """)
            result["sample"] = sample
    except Exception as e:
        result["error"] = str(e)
    return result


# ------------------------------------------------------------------
# Probe 6: pipeline progress check via directive queue
# ------------------------------------------------------------------
def probe_directive_queue(limit: int = 10) -> dict[str, Any]:
    result: dict[str, Any] = {
        "table_exists": False,
        "pending_count": None,
        "recent_pending": [],
        "blocked_by_registry_error": False,
        "error": None,
    }
    try:
        # Check if proposed/pending tables exist and how many rows
        proposed = ws_query("""
            SELECT COUNT(*) AS cnt FROM information_schema.tables
            WHERE table_name = 'directives_proposed'
        """)
        if proposed and proposed[0]["cnt"] > 0:
            result["table_exists"] = True
            rows = ws_query("SELECT COUNT(*) AS cnt FROM directives_proposed")
            result["pending_count"] = rows[0]["cnt"] if rows else 0

            recent = ws_query(f"""
                SELECT directive_id, context_type, complexity, created_at
                FROM directives_proposed
                ORDER BY created_at DESC
                LIMIT {limit}
            """)
            result["recent_pending"] = recent
    except Exception as e:
        result["error"] = str(e)
    return result


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def run() -> None:
    findings: dict[str, Any] = {
        "diagnostic": "check_registry_read_error_source",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "write_service_url": WRITE_SERVICE_URL,
        "probes": {},
        "summary": {
            "write_service_reachable": False,
            "registry_errors_found": False,
            "pipeline_blocked": False,
            "offending_services": [],
            "blocking_assessment": "unknown",
        },
    }

    # Run all probes
    findings["probes"]["connectivity"] = probe_connectivity()
    findings["probes"]["service_health"] = probe_service_health_errors()
    findings["probes"]["audit_log"] = probe_audit_log()
    findings["probes"]["build_provenance"] = probe_build_provenance()
    findings["probes"]["registry_table"] = probe_registry_table()
    findings["probes"]["directive_queue"] = probe_directive_queue()

    # Derive summary
    conn = findings["probes"]["connectivity"]
    findings["summary"]["write_service_reachable"] = conn.get("ok", False)

    sh = findings["probes"]["service_health"]
    reg_errs = sh.get("registry_error_rows", [])
    findings["summary"]["registry_errors_found"] = len(reg_errs) > 0
    findings["summary"]["offending_services"] = list(
        {r.get("service") for r in reg_errs if r.get("service")}
    )

    audit = findings["probes"]["audit_log"]
    if audit.get("registry_read_error_rows"):
        findings["summary"]["registry_errors_found"] = True

    # Blocking assessment
    bp = findings["probes"]["build_provenance"]
    recent_fails = bp.get("recent_failures", [])
    registry_fails = bp.get("registry_related_failures", [])

    if not conn.get("ok"):
        findings["summary"]["blocking_assessment"] = "CRITICAL - write_service unreachable"
    elif len(reg_errs) > 0:
        # Check if errors are from a critical pipeline service
        critical = {"signal_analyser", "mcp_scanner", "enrichment_pipeline",
                    "directive_generator", "gate_scheduler"}
        offenders = {s for s in findings["summary"]["offending_services"]}
        if offenders & critical:
            findings["summary"]["blocking_assessment"] = (
                "LIKELY BLOCKING - critical pipeline service reporting registry error"
            )
            findings["summary"]["pipeline_blocked"] = True
        else:
            findings["summary"]["blocking_assessment"] = (
                "NON-CRITICAL - non-pipeline service reporting registry error"
            )
    elif len(registry_fails) > 0:
        findings["summary"]["blocking_assessment"] = (
            "POSSIBLE BLOCKING - recent build failures reference registry"
        )
        findings["summary"]["pipeline_blocked"] = True
    else:
        findings["summary"]["blocking_assessment"] = "NO REGISTRY ERRORS DETECTED"

    print(json.dumps(findings, indent=2, default=str))


if __name__ == "__main__":
    run()
