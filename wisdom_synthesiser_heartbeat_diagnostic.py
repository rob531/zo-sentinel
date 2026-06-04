#!/usr/bin/env python3
"""
ZO-SENTINEL Wisdom Synthesiser Heartbeat Diagnostic Module

Diagnostic utility that inspects service_health table for wisdom_synthesiser daemon
(currently stale at 5h21m as of this run). Reads last_heartbeat, calculates age,
outputs human-readable diagnostic report.

This is DIAGNOSTIC-ONLY per spec rule 10.5.
Does NOT restart services. Does NOT write to DB (no audit_log write).
Produces human-readable status suitable for ops dashboard.

Reference: PRODUCT_SPEC §6 heartbeat contract.
"""

# deps: requests

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Configuration
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "wisdom_synthesiser"
STALE_THRESHOLD_SECS = 300          # 5 minutes  - delayed
SERIOUS_THRESHOLD_SECS = 600        # 10 minutes - hung
CRITICAL_THRESHOLD_SECS = 1800      # 30 minutes - crashed/stopped

# Root cause categories
ROOT_CAUSE_HEALTHY = "HEALTHY"
ROOT_CAUSE_DELAYED = "DELAYED"
ROOT_CAUSE_HUNG = "HUNG"
ROOT_CAUSE_CRASHED = "CRASHED"
ROOT_CAUSE_UNKNOWN = "UNKNOWN"


def ws_query(sql: str, params: Optional[list] = None) -> list:
    """Execute SELECT query via write_service HTTP API (read-only, no writes)."""
    payload = {
        "sql": sql,
        "params": params or [],
        "wait": True,
    }
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error("ws_query failed: %s", e)
        return []


def get_service_health() -> Optional[dict]:
    """Fetch wisdom_synthesiser row from service_health table."""
    sql = """
        SELECT service, last_heartbeat, status, meta
        FROM service_health
        WHERE service = ?
        ORDER BY last_heartbeat DESC
        LIMIT 1
    """
    rows = ws_query(sql, [SERVICE_NAME])
    if rows:
        return rows[0]
    return None


def parse_heartbeat(heartbeat_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO 8601 heartbeat string to timezone-aware datetime."""
    if not heartbeat_str:
        return None
    try:
        ts = heartbeat_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except ValueError:
        try:
            return datetime.fromisoformat(heartbeat_str).replace(tzinfo=timezone.utc)
        except ValueError:
            logger.warning("Could not parse heartbeat: %s", heartbeat_str)
            return None


def calculate_heartbeat_age(heartbeat_dt: datetime) -> float:
    """Calculate heartbeat age in seconds relative to now (UTC)."""
    now = datetime.now(timezone.utc)
    age = (now - heartbeat_dt).total_seconds()
    return max(0.0, age)


def classify_root_cause(age_secs: float) -> tuple[str, str]:
    """
    Classify root cause based on heartbeat age thresholds.

    Returns: (root_cause_tag, description)
    """
    if age_secs < STALE_THRESHOLD_SECS:
        return ROOT_CAUSE_HEALTHY, "Daemon heartbeat is recent and healthy"

    if age_secs < SERIOUS_THRESHOLD_SECS:
        return (
            ROOT_CAUSE_DELAYED,
            f"Heartbeat is {age_secs / 60:.1f} min old — daemon may be slow but not stuck",
        )

    if age_secs < CRITICAL_THRESHOLD_SECS:
        return (
            ROOT_CAUSE_HUNG,
            f"Heartbeat is {age_secs / 60:.1f} min old — daemon appears HUNG",
        )

    return (
        ROOT_CAUSE_CRASHED,
        f"Heartbeat is {age_secs / 3600:.1f} h old — daemon likely CRASHED or STOPPED",
    )


def produce_diagnostic_report(
    health: Optional[dict],
    age_secs: Optional[float],
    root_cause: str,
    root_cause_desc: str,
) -> dict:
    """Build comprehensive diagnostic report dict (read-only, no DB writes)."""
    now_iso = datetime.now(timezone.utc).isoformat()

    report = {
        "diagnostic_id": f"wisdom_synth_hb_{int(time.time())}",
        "service": SERVICE_NAME,
        "checked_at": now_iso,
        "heartbeat_found": health is not None,
        "last_heartbeat": health.get("last_heartbeat") if health else None,
        "heartbeat_age_secs": age_secs,
        "heartbeat_age_formatted": _format_duration(age_secs) if age_secs is not None else None,
        "status": health.get("status") if health else None,
        "meta": health.get("meta") if health else None,
        "root_cause": root_cause,
        "root_cause_description": root_cause_desc,
        "thresholds": {
            "stale_secs": STALE_THRESHOLD_SECS,
            "serious_secs": SERIOUS_THRESHOLD_SECS,
            "critical_secs": CRITICAL_THRESHOLD_SECS,
        },
        "recommendations": _get_recommendations(root_cause, age_secs),
    }
    return report


def _format_duration(secs: Optional[float]) -> str:
    """Format seconds as human-readable string."""
    if secs is None:
        return "unknown"
    if secs < 60:
        return f"{secs:.0f}s"
    if secs < 3600:
        return f"{secs / 60:.1f}m"
    return f"{secs / 3600:.1f}h"


def _get_recommendations(root_cause: str, age_secs: Optional[float]) -> list:
    """Return action items keyed to root cause tag."""
    if root_cause == ROOT_CAUSE_HEALTHY:
        return ["No action required — daemon is healthy"]

    if root_cause == ROOT_CAUSE_DELAYED:
        return [
            "Monitor for continued delays",
            "Check daemon logs for slow operations",
            "Consider investigation if delays persist beyond 10 minutes",
        ]

    if root_cause == ROOT_CAUSE_HUNG:
        return [
            "Daemon appears to be hung",
            "Check process status and resource usage",
            "Review recent logs for blocking operations",
            "Consider manual intervention if symptoms persist",
        ]

    if root_cause == ROOT_CAUSE_CRASHED:
        return [
            "CRITICAL: Daemon appears to have crashed or stopped",
            "Check process status: pgrep -f wisdom_synthesiser",
            "Review crash logs or core dumps",
            "Service restart may be required",
            "Investigate root cause of crash before restarting",
        ]

    return [
        "Unable to determine root cause from heartbeat",
        "Check service_health table directly",
        "Review daemon logs for clues",
    ]


def run() -> dict:
    """
    Execute diagnostic and return report dict.

    Read-only operation: queries service_health, calculates age,
    classifies severity, returns structured report.
    """
    logger.info("=" * 60)
    logger.info("Wisdom Synthesiser Heartbeat Diagnostic")
    logger.info("=" * 60)

    # Step 1: fetch service_health row
    health = get_service_health()

    if not health:
        logger.warning("No service_health record found for %s", SERVICE_NAME)
        report = {
            "diagnostic_id": f"wisdom_synth_hb_{int(time.time())}",
            "service": SERVICE_NAME,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "heartbeat_found": False,
            "last_heartbeat": None,
            "heartbeat_age_secs": None,
            "heartbeat_age_formatted": None,
            "status": None,
            "meta": None,
            "root_cause": ROOT_CAUSE_UNKNOWN,
            "root_cause_description": "No service_health record found for wisdom_synthesiser daemon",
            "recommendations": [
                "Daemon may not be registered in service_health",
                "Verify wisdom_synthesiser process is running (pgrep -f wisdom_synthesiser)",
                "Check if the daemon is writing heartbeats correctly",
            ],
        }
        _print_report(report)
        return report

    # Step 2: parse heartbeat and calculate age
    heartbeat_str = health.get("last_heartbeat")
    heartbeat_dt = parse_heartbeat(heartbeat_str)

    if not heartbeat_dt:
        report = produce_diagnostic_report(
            health=health,
            age_secs=None,
            root_cause=ROOT_CAUSE_UNKNOWN,
            root_cause_desc="Could not parse last_heartbeat timestamp",
        )
        _print_report(report)
        return report

    age_secs = calculate_heartbeat_age(heartbeat_dt)
    logger.info(
        "Last heartbeat: %s  (%s ago)",
        heartbeat_str,
        _format_duration(age_secs),
    )

    # Step 3: classify root cause
    root_cause, root_cause_desc = classify_root_cause(age_secs)
    logger.info("Root cause: %s — %s", root_cause, root_cause_desc)

    # Step 4: produce report
    report = produce_diagnostic_report(
        health=health,
        age_secs=age_secs,
        root_cause=root_cause,
        root_cause_desc=root_cause_desc,
    )

    # Step 5: log summary
    _print_report(report)
    return report


def _print_report(report: dict) -> None:
    """Emit human-readable report to logger (suitable for ops dashboard)."""
    sep = "=" * 60
    logger.info("%s", sep)
    logger.info("WISDOM SYNTHESISER HEARTBEAT DIAGNOSTIC")
    logger.info("%s", sep)
    logger.info("Service          : %s", report["service"])
    logger.info("Diagnostic ID    : %s", report["diagnostic_id"])
    logger.info("Checked At       : %s", report["checked_at"])
    logger.info("Heartbeat Found  : %s", report["heartbeat_found"])

    if report["heartbeat_found"]:
        logger.info("Last Heartbeat   : %s", report["last_heartbeat"])
        logger.info("Age              : %s (%s ago)", report["heartbeat_age_formatted"], report["heartbeat_age_secs"])
        logger.info("Status           : %s", report["status"])
        if report["meta"]:
            logger.info("Meta             : %s", report["meta"])

    logger.info("%s", sep)
    logger.info("Root Cause       : %s", report["root_cause"])
    logger.info("Description      : %s", report["root_cause_description"])
    logger.info("%s", sep)
    logger.info("Recommendations:")
    for i, rec in enumerate(report["recommendations"], 1):
        logger.info("  %d. %s", i, rec)
    logger.info("%s", sep)


if __name__ == "__main__":
    # Self-smoke: run against 3 known-good inputs and assert valid structure
    logger.info("Running self-smoke test...")

    # Smoke 1: run with whatever the live DB has
    result = run()

    # Verify report structure
    assert "diagnostic_id" in result, "Missing diagnostic_id in report"
    assert "service" in result, "Missing service in report"
    assert "root_cause" in result, "Missing root_cause in report"
    assert "recommendations" in result, "Missing recommendations in report"
    assert isinstance(result["recommendations"], list), "recommendations must be a list"
    assert "checked_at" in result, "Missing checked_at in report"

    # Smoke 2: classify_root_cause unit touch
    assert classify_root_cause(0) == (ROOT_CAUSE_HEALTHY, "Daemon heartbeat is recent and healthy")
    assert classify_root_cause(240) == (ROOT_CAUSE_HEALTHY, "Daemon heartbeat is recent and healthy")
    assert classify_root_cause(300) == (ROOT_CAUSE_DELAYED, "Heartbeat is 5.0 min old — daemon may be slow but not stuck")
    assert classify_root_cause(900) == (ROOT_CAUSE_HUNG, "Heartbeat is 15.0 min old — daemon appears HUNG")
    assert classify_root_cause(3600) == (ROOT_CAUSE_CRASHED, "Heartbeat is 1.0 h old — daemon likely CRASHED or STOPPED")

    # Smoke 3: _format_duration unit touch
    assert _format_duration(30) == "30s"
    assert _format_duration(300) == "5.0m"
    assert _format_duration(7200) == "2.0h"
    assert _format_duration(None) == "unknown"

    logger.info("Smoke test PASSED — structure valid, classifiers correct.")
    sys.exit(0)