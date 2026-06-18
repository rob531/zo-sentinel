#!/usr/bin/env python3
"""
Enrichment Pipeline Health Diagnostic

Verifies the health of the enrichment pipeline given:
- mcp_signal_enrichments: 12 rows (anomalously low)
- mcp_signal_scores: 1,313,483 rows
- mcp_signal_enrichments_writer.py: quarantined

Reports findings without proposing rebuilds of quarantined files.
"""

import json
import socket
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"
WRITE_TIMEOUT = 10


def ws_query(sql: str, params: list = None) -> list:
    """Query via write_service /query endpoint."""
    payload = {"sql": sql, "params": params or []}
    resp = requests.post(
        f"{WRITE_SERVICE}/query", json=payload, timeout=WRITE_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", data.get("rows", []))


def ws_execute(sql: str, params: list = None) -> bool:
    """Execute DDL/DML via write_service /execute endpoint."""
    payload = {"sql": sql, "params": params or [], "wait": True}
    resp = requests.post(
        f"{WRITE_SERVICE}/execute", json=payload, timeout=WRITE_TIMEOUT
    )
    return resp.status_code == 200


def test_write_service_connectivity(host: str, port: int) -> Dict[str, Any]:
    """Test TCP connectivity to the enrichment write service."""
    result: Dict[str, Any] = {
        "host": host,
        "port": port,
        "reachable": False,
        "latency_ms": None,
        "error": None,
    }

    try:
        start = datetime.now()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        sock.close()
        end = datetime.now()

        result["reachable"] = True
        result["latency_ms"] = (end - start).total_seconds() * 1000
    except socket.timeout:
        result["error"] = "Connection timed out (5s)"
    except ConnectionRefusedError:
        result["error"] = "Connection refused - service not running or port blocked"
    except socket.gaierror as e:
        result["error"] = f"DNS/hostname resolution failed: {e}"
    except Exception as e:
        result["error"] = f"Unexpected error: {str(e)}"

    return result


def test_write_service_http() -> Dict[str, Any]:
    """Test HTTP API connectivity to write_service."""
    result: Dict[str, Any] = {
        "query_endpoint_ok": False,
        "execute_endpoint_ok": False,
        "error": None,
    }

    try:
        # Test /query endpoint
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": "SELECT 1 AS test", "params": []},
            timeout=WRITE_TIMEOUT,
        )
        result["query_endpoint_ok"] = resp.status_code == 200
    except Exception as e:
        result["error"] = f"Query endpoint failed: {e}"

    try:
        # Test /execute endpoint
        resp = requests.post(
            f"{WRITE_SERVICE}/execute",
            json={"sql": "SELECT 1 AS test", "params": [], "wait": True},
            timeout=WRITE_TIMEOUT,
        )
        result["execute_endpoint_ok"] = resp.status_code == 200
    except Exception as e:
        result["execute_error"] = f"Execute endpoint failed: {e}"

    return result


def check_database_counts() -> Dict[str, Any]:
    """Check enrichment-related table counts via write_service."""
    result: Dict[str, Any] = {
        "enrichments_count": None,
        "scores_count": None,
        "enrichment_rate": None,
        "connection_status": None,
        "error": None,
    }

    try:
        # Count enrichments
        rows = ws_query("SELECT COUNT(*) AS count FROM mcp_signal_enrichments")
        if rows:
            result["enrichments_count"] = rows[0].get("count", rows[0].get("COUNT(*)", 0))
        else:
            # Try positional
            result["enrichments_count"] = 0

        # Count scores
        rows = ws_query("SELECT COUNT(*) AS count FROM mcp_signal_scores")
        if rows:
            result["scores_count"] = rows[0].get("count", rows[0].get("COUNT(*)", 0))
        else:
            result["scores_count"] = 0

        # Calculate rate
        if result["scores_count"] and result["scores_count"] > 0:
            result["enrichment_rate"] = result["enrichments_count"] / result["scores_count"]

        result["connection_status"] = "connected"

    except requests.exceptions.ConnectionError as e:
        result["error"] = f"Connection failed: {e}"
        result["connection_status"] = "failed"
    except Exception as e:
        result["error"] = str(e)
        result["connection_status"] = "failed"

    return result


def check_last_enrichment_time() -> Optional[str]:
    """Get the timestamp of the most recent enrichment."""
    try:
        rows = ws_query(
            "SELECT MAX(scored_at) AS last_enrichment FROM mcp_signal_enrichments"
        )
        if rows and rows[0]:
            return rows[0].get("last_enrichment") or rows[0].get("MAX(scored_at)")
    except Exception:
        pass
    return None


def check_signal_types_present() -> list:
    """Check which signal types have enrichment data."""
    try:
        rows = ws_query("SELECT DISTINCT signal_type FROM mcp_signal_enrichments")
        return [r.get("signal_type") for r in rows if r.get("signal_type")]
    except Exception:
        return []


def check_harness_exists(harness_path: str) -> bool:
    """Check if enrichment_harness.py exists."""
    try:
        with open(harness_path, "r") as f:
            return True
    except FileNotFoundError:
        return False


def check_writer_quarantined() -> Dict[str, Any]:
    """Check status of the quarantined writer file."""
    quarantine_paths = [
        "/home/workspace/zo_sentinel/quarantine/mcp_signal_enrichments_writer.py",
        "/opt/mcp/quarantine/mcp_signal_enrichments_writer.py",
        "/var/quarantine/mcp_signal_enrichments_writer.py",
    ]

    result: Dict[str, Any] = {
        "quarantined": True,
        "quarantine_paths_found": [],
        "systemd_status": None,
        "supervisor_status": None,
    }

    for path in quarantine_paths:
        try:
            with open(path, "r") as f:
                result["quarantine_paths_found"].append(path)
        except FileNotFoundError:
            pass

    # Check systemd status
    import subprocess

    try:
        res = subprocess.run(
            ["systemctl", "is-active", "mcp-enrichment-writer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            result["systemd_status"] = res.stdout.strip()
    except Exception:
        pass

    # Check supervisor status
    try:
        res = subprocess.run(
            ["supervisorctl", "status", "mcp-enrichment-writer"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            result["supervisor_status"] = res.stdout.strip()
    except Exception:
        pass

    return result


def generate_report(
    db_info: Dict[str, Any],
    connectivity: Dict[str, Any],
    http_api: Dict[str, Any],
    writer_status: Dict[str, Any],
    last_enrichment: Optional[str],
    signal_types: list,
) -> str:
    """Generate human-readable diagnostic report."""

    lines = []
    lines.append("=" * 70)
    lines.append("ENRICHMENT PIPELINE HEALTH DIAGNOSTIC REPORT")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append("=" * 70)

    # Section 1: Database Health
    lines.append("\n" + "-" * 40)
    lines.append("1. DATABASE HEALTH")
    lines.append("-" * 40)

    if db_info.get("connection_status") == "connected":
        lines.append("  [OK] Database Connection: OK")
        scores = db_info.get("scores_count", 0)
        enrichments = db_info.get("enrichments_count", 0)
        rate = db_info.get("enrichment_rate", 0)

        lines.append(f"  - mcp_signal_scores row count:      {scores:,}")
        lines.append(f"  - mcp_signal_enrichments row count: {enrichments:,}")
        lines.append(f"  - Enrichment rate:                  {rate:.4%}")

        if rate < 0.01:
            lines.append("")
            lines.append("  [CRITICAL] Enrichment rate is critically low (<1%)")
            lines.append("             Expected: Near 100% of scores should have enrichments")

        if signal_types:
            lines.append(f"  - Signal types present: {', '.join(signal_types)}")
        else:
            lines.append("  - Signal types present: none")
    else:
        lines.append("  [FAIL] Database Connection: FAILED")
        lines.append(f"  - Error: {db_info.get('error', 'Unknown error')}")

    # Section 2: Write Service Connectivity
    lines.append("\n" + "-" * 40)
    lines.append("2. WRITE SERVICE CONNECTIVITY")
    lines.append("-" * 40)
    lines.append(f"  Target: {connectivity.get('host')}:{connectivity.get('port')}")

    if connectivity.get("reachable"):
        lines.append("  [OK] TCP Socket: REACHABLE")
        lines.append(f"  - Latency: {connectivity.get('latency_ms', 'N/A'):.2f}ms")
    else:
        lines.append("  [FAIL] TCP Socket: UNREACHABLE")
        lines.append(f"  - Error: {connectivity.get('error', 'Unknown')}")

    lines.append("")
    if http_api.get("query_endpoint_ok"):
        lines.append("  [OK] HTTP /query endpoint: OK")
    else:
        lines.append("  [FAIL] HTTP /query endpoint: FAIL")
        lines.append(f"       {http_api.get('error', 'Unknown')}")

    if http_api.get("execute_endpoint_ok"):
        lines.append("  [OK] HTTP /execute endpoint: OK")
    else:
        err = http_api.get("execute_error", "Unknown")
        lines.append(f"  [WARN] HTTP /execute endpoint: {err}")

    # Section 3: Last Enrichment Timestamp
    lines.append("\n" + "-" * 40)
    lines.append("3. ENRICHMENT TIMELINE")
    lines.append("-" * 40)

    if last_enrichment:
        lines.append(f"  - Last enrichment timestamp: {last_enrichment}")
    else:
        lines.append("  - Last enrichment timestamp: UNKNOWN (table may be empty)")

    # Section 4: Writer Service Status
    lines.append("\n" + "-" * 40)
    lines.append("4. WRITER SERVICE STATUS")
    lines.append("-" * 40)
    lines.append("  Status: QUARANTINED (mcp_signal_enrichments_writer.py)")

    if writer_status.get("quarantine_paths_found"):
        lines.append("  - Quarantine paths found:")
        for path in writer_status["quarantine_paths_found"]:
            lines.append(f"    * {path}")
    else:
        lines.append("  - No quarantine copies found")

    systemd = writer_status.get("systemd_status")
    supervisor = writer_status.get("supervisor_status")

    if systemd:
        lines.append(f"  - Systemd service: {systemd}")
    if supervisor:
        lines.append(f"  - Supervisor service: {supervisor}")

    if not systemd and not supervisor:
        lines.append("  - No systemd/supervisor service registration found")

    # Section 5: Root Cause Analysis
    lines.append("\n" + "-" * 40)
    lines.append("5. ROOT CAUSE ANALYSIS")
    lines.append("-" * 40)

    issues = []

    rate = db_info.get("enrichment_rate", 1)
    if rate < 0.01:
        issues.append(
            f"CRITICAL: Only {db_info.get('enrichments_count', 0):,} enrichment "
            f"records exist for {db_info.get('scores_count', 0):,} scores"
        )

    if not connectivity.get("reachable"):
        issues.append(
            "BLOCKING: Write service is unreachable on port 8772 (TCP)"
        )

    if not http_api.get("query_endpoint_ok"):
        issues.append(
            "BLOCKING: Write service HTTP /query endpoint is not responding"
        )

    if not systemd and not supervisor:
        issues.append(
            "BLOCKING: Writer service is not registered in systemd/supervisor"
        )

    if issues:
        for i, issue in enumerate(issues, 1):
            lines.append(f"  {i}. {issue}")
    else:
        lines.append("  No obvious issues detected.")

    # Section 6: Findings (NO REBUILD RECOMMENDATIONS)
    lines.append("\n" + "-" * 40)
    lines.append("6. FINDINGS")
    lines.append("-" * 40)
    lines.append("  (No rebuild recommendations per task requirements)")

    if not connectivity.get("reachable") or not http_api.get("query_endpoint_ok"):
        lines.append("")
        lines.append("  PRIMARY CAUSE: Write service connectivity issue")
        lines.append("  - The enrichment writer cannot connect to write_service:8772")
        lines.append("  - This explains the enrichment deficit")

    if not systemd and not supervisor:
        lines.append("")
        lines.append("  SECONDARY CAUSE: Writer service not running")
        lines.append("  - mcp_signal_enrichments_writer.py is quarantined")
        lines.append("  - Service is not registered or not running")
        lines.append("  - Cannot process new enrichments without service restoration")

    if last_enrichment:
        lines.append("")
        lines.append(f"  Last enrichment recorded: {last_enrichment}")

    lines.append("\n" + "=" * 70)
    lines.append("END OF DIAGNOSTIC REPORT")
    lines.append("=" * 70)

    return "\n".join(lines)


def main() -> int:
    """Run all diagnostics and output report."""

    print("Running Enrichment Pipeline Health Diagnostics...")
    print()

    # Run all checks
    connectivity = test_write_service_connectivity("127.0.0.1", 8772)
    http_api = test_write_service_http()
    db_info = check_database_counts()
    writer_status = check_writer_quarantined()
    last_enrichment = check_last_enrichment_time()
    signal_types = check_signal_types_present()

    # Generate and print report
    report = generate_report(
        db_info, connectivity, http_api, writer_status,
        last_enrichment, signal_types
    )
    print(report)

    # Build JSON summary
    critical_issues = 0
    if not connectivity.get("reachable"):
        critical_issues += 1
    if not http_api.get("query_endpoint_ok"):
        critical_issues += 1
    if db_info.get("enrichment_rate", 1) < 0.01:
        critical_issues += 1

    summary = {
        "timestamp": datetime.now().isoformat(),
        "database": db_info,
        "connectivity": connectivity,
        "http_api": http_api,
        "writer_status": writer_status,
        "last_enrichment_time": last_enrichment,
        "signal_types": signal_types,
        "critical_issues_count": critical_issues,
    }

    print("\n\n--- JSON SUMMARY ---")
    print(json.dumps(summary, default=str, indent=2))

    return 1 if critical_issues > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
