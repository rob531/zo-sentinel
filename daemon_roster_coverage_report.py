#!/usr/bin/env python3
"""
daemon_roster_coverage_report.py

Utility to generate a coverage/completeness report for the live daemon roster
against the declared KNOWN_DAEMONS registry.

The module relies on the application's SQLAlchemy session (app.db.get_session)
and the corresponding ORM models (app.models.McpServerRegistry,
app.models.ServiceHealth). No direct file or external database access is
performed.
"""

from __future__ import annotations

import datetime
import sys
from typing import Dict, List

# --------------------------------------------------------------------------- #
# Known daemons – normally imported from sentinel_directive_generator.py.
# --------------------------------------------------------------------------- #
KNOWN_DAEMONS: List[str] = [
    "daemon_alpha",
    "daemon_beta",
    "daemon_gamma",
    "daemon_delta",
    "daemon_epsilon",
]  # Example list; replace with the real constant as needed.

# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    """Current UTC time in ISO‑8601 format."""
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()


def _is_recent(ts: datetime.datetime, minutes: int = 5) -> bool:
    """Return True if *ts* is within *minutes* of now."""
    delta = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc) - ts
    return delta.total_seconds() <= minutes * 60


def generate_roster_coverage_report() -> Dict:
    """
    Build a coverage report.

    Returns
    -------
    dict
        {
            "generated_at": ISO8601 timestamp,
            "total_declared": int,
            "healthy": [ {"name": str, "last_seen": ISO8601, "status": str}, ... ],
            "stale":   [ {"name": str, "last_seen": ISO8601, "status": str}, ... ],
            "never_seen": [str, ...],
            "stale_rate_pct": float,
            "recommendations": [str, ...],
        }
    """
    # Import inside the function to avoid import‑time side effects.
    from app.db import get_session
    from app.models import McpServerRegistry, ServiceHealth

    # ------------------------------------------------------------------- #
    # Gather data from the DB
    # ------------------------------------------------------------------- #
    session = get_session()
    try:
        registry_rows = session.query(McpServerRegistry).all()
        health_rows = session.query(ServiceHealth).all()
    finally:
        session.close()

    # Map server_id -> last_seen (datetime)
    registry_map: Dict[str, datetime.datetime] = {
        row.server_id: row.last_seen.replace(tzinfo=datetime.timezone.utc)
        for row in registry_rows
    }

    # Map service -> (last_heartbeat, status)
    health_map: Dict[str, tuple[datetime.datetime, str]] = {
        row.service: (
            row.last_heartbeat.replace(tzinfo=datetime.timezone.utc),
            row.status,
        )
        for row in health_rows
    }

    healthy: List[Dict] = []
    stale: List[Dict] = []
    never_seen: List[str] = []

    for daemon in KNOWN_DAEMONS:
        if daemon not in registry_map:
            never_seen.append(daemon)
            continue

        last_seen = registry_map[daemon]
        status = health_map.get(daemon, (None, "unknown"))[1]

        daemon_info = {
            "name": daemon,
            "last_seen": last_seen.isoformat(),
            "status": status,
        }

        if _is_recent(last_seen):
            healthy.append(daemon_info)
        else:
            stale.append(daemon_info)

    total_declared = len(KNOWN_DAEMONS)
    stale_rate_pct = (len(stale) / total_declared * 100) if total_declared else 0.0

    # Simple recommendation logic
    recommendations: List[str] = []
    if stale_rate_pct == 0:
        recommendations.append("All declared daemons are healthy.")
    elif stale_rate_pct < 20:
        recommendations.append("Minor stale daemons detected; investigate individually.")
    elif stale_rate_pct < 50:
        recommendations.append(
            "Significant number of stale daemons; consider restarting affected services."
        )
    else:
        recommendations.append(
            "High stale rate; immediate operational review required."
        )
    if never_seen:
        recommendations.append(
            f"{len(never_seen)} daemon(s) have never reported a heartbeat; verify deployment."
        )

    report = {
        "generated_at": _now_iso(),
        "total_declared": total_declared,
        "healthy": healthy,
        "stale": stale,
        "never_seen": never_seen,
        "stale_rate_pct": round(stale_rate_pct, 2),
        "recommendations": recommendations,
    }
    return report


# --------------------------------------------------------------------------- #
# CLI entry point for quick self‑test
# --------------------------------------------------------------------------- #
def _print_table(report: Dict) -> None:
    """Print a simple tabular view of the report."""
    print(f"Generated at: {report['generated_at']}")
    print(f"Total declared daemons: {report['total_declared']}")
    print(f"Healthy: {len(report['healthy'])}")
    print(f"Stale: {len(report['stale'])}")
    print(f"Never seen: {len(report['never_seen'])}")
    print(f"Stale rate (%): {report['stale_rate_pct']:.2f}")
    print("\nRecommendations:")
    for rec in report["recommendations"]:
        print(f" - {rec}")

    # Detailed rows (optional)
    if report["healthy"]:
        print("\nHealthy daemons:")
        for d in report["healthy"]:
            print(f" * {d['name']} (last_seen={d['last_seen']}, status={d['status']})")
    if report["stale"]:
        print("\nStale daemons:")
        for d in report["stale"]:
            print(f" * {d['name']} (last_seen={d['last_seen']}, status={d['status']})")
    if report["never_seen"]:
        print("\nNever‑seen daemons:")
        for name in report["never_seen"]:
            print(f" * {name}")


def _self_test() -> None:
    """Run a minimal self‑test when executed as a script."""
    report = generate_roster_coverage_report()
    assert report["total_declared"] >= 0, "total_declared must be non‑negative"
    assert 0.0 <= report["stale_rate_pct"] <= 100.0, "stale_rate_pct out of bounds"
    _print_table(report)
    print("\nPASS")
    sys.exit(0)


if __name__ == "__main__":
    _self_test()