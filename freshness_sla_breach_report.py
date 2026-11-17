"""freshness_sla_breach_report.py -- PRODUCT_SPEC section 4 freshness SLA audit.

The spec's freshness SLAs (the "state management" contract):
  * a new MCP must have a FIRST VERDICT within 24h of `first_seen`;
  * every live MCP must be RE-VERDICT-ED within 30 days of its `last_assessed`.

This module is a REPORT generator, not an HTTP router. The audit logic is a
PURE function over plain dicts so it is testable without any data layer:

    build_freshness_sla_breach_report(registry_rows, now=None) -> dict

`main()` is the thin CLI wrapper: it reads the real registry rows from the app
data layer (`app.db` session -> `app.models.McpServerRegistry`), builds the
report and prints a human-readable summary.

BASIS for every number (stated so the report cannot be over-claimed):
  * first-verdict lag is measured from `first_seen` to the assessment that
    produced the verdict (`last_assessed`); servers with no verdict at all are
    reported separately as missing, never as a fabricated lag;
  * re-verdict age is measured from `last_assessed` to the reference instant;
  * a server with no `last_assessed` is reported as missing an assessment,
    never as an infinite breach.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db import SessionLocal
from app.models import McpServerRegistry

# Freshness SLA thresholds (PRODUCT_SPEC section 4).
FIRST_VERDICT_SLA_HOURS = 24
REASSESSMENT_SLA_DAYS = 30

MISSING_VERDICT_VALUES = ("", "unreviewed")
WORST_OFFENDER_LIMIT = 10


def _parse_ts(value: Any) -> Optional[datetime]:
    """ISO string / datetime -> naive UTC datetime. None when unusable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value).strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


def _days_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 86400.0


def build_freshness_sla_breach_report(registry_rows: List[dict],
                                      now: Optional[datetime] = None) -> dict:
    """Audit the freshness SLAs over registry rows. Pure: no DB, no network.

    registry_rows: dicts with keys server_id, first_seen, last_assessed,
    risk_tier, verdict (timestamps as ISO strings or datetimes).
    """
    reference = now if now is not None else datetime.utcnow()
    if isinstance(reference, datetime) and reference.tzinfo is not None:
        reference = reference.astimezone(timezone.utc).replace(tzinfo=None)

    missing_first_verdict: List[str] = []
    missing_last_assessment: List[str] = []
    first_verdict_beyond_24h: List[dict] = []
    reassessment_beyond_30d: List[dict] = []
    by_risk_tier: Dict[str, int] = {}

    for row in registry_rows or []:
        row = row or {}
        server_id = str(row.get("server_id") or "").strip()
        tier = str(row.get("risk_tier") or "").strip().upper() or "UNKNOWN"
        by_risk_tier[tier] = by_risk_tier.get(tier, 0) + 1

        verdict = row.get("verdict")
        verdict_missing = verdict is None or str(verdict).strip().lower() in MISSING_VERDICT_VALUES
        if verdict_missing:
            missing_first_verdict.append(server_id)

        first_seen = _parse_ts(row.get("first_seen"))
        last_assessed = _parse_ts(row.get("last_assessed"))

        if last_assessed is None:
            missing_last_assessment.append(server_id)

        # First-verdict SLA: only measurable when a verdict exists AND the
        # producing assessment is timestamped.
        if (not verdict_missing) and first_seen is not None and last_assessed is not None:
            lag_hours = _hours_between(last_assessed, first_seen)
            if lag_hours > FIRST_VERDICT_SLA_HOURS:
                first_verdict_beyond_24h.append({
                    "server_id": server_id,
                    "hours_over": round(lag_hours - FIRST_VERDICT_SLA_HOURS, 2),
                })

        # Re-verdict SLA: age of the last assessment against the reference.
        if last_assessed is not None:
            age_days = _days_between(reference, last_assessed)
            if age_days > REASSESSMENT_SLA_DAYS:
                reassessment_beyond_30d.append({
                    "server_id": server_id,
                    "days_over": round(age_days - REASSESSMENT_SLA_DAYS, 2),
                })

    first_verdict_beyond_24h.sort(key=lambda d: (-d["hours_over"], d["server_id"]))
    reassessment_beyond_30d.sort(key=lambda d: (-d["days_over"], d["server_id"]))

    worst_offenders = [
        {"server_id": d["server_id"], "hours_over": d["hours_over"]}
        for d in first_verdict_beyond_24h[:WORST_OFFENDER_LIMIT]
    ]

    return {
        "total": len(registry_rows or []),
        "missing_first_verdict": missing_first_verdict,
        "missing_last_assessment": missing_last_assessment,
        "first_verdict_beyond_24h": first_verdict_beyond_24h,
        "reassessment_beyond_30d": reassessment_beyond_30d,
        "by_risk_tier": by_risk_tier,
        "worst_offenders": worst_offenders,
    }


def _read_registry_rows() -> List[dict]:
    """Read the live registry rows from the app data layer (read-only)."""
    from sqlalchemy import select

    session = SessionLocal()
    try:
        rows = session.execute(select(
            McpServerRegistry.server_id,
            McpServerRegistry.first_seen,
            McpServerRegistry.last_assessed,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
        )).all()
        return [{
            "server_id": r[0],
            "first_seen": r[1],
            "last_assessed": r[2],
            "risk_tier": r[3],
            "verdict": r[4],
        } for r in rows]
    finally:
        session.close()


def _print_summary(report: dict) -> None:
    print("Freshness SLA breach report")
    print(f"  servers audited                : {report['total']}")
    print(f"  missing first verdict          : {len(report['missing_first_verdict'])}")
    print(f"  missing last assessment        : {len(report['missing_last_assessment'])}")
    print(f"  first verdict beyond {FIRST_VERDICT_SLA_HOURS}h      : "
          f"{len(report['first_verdict_beyond_24h'])}")
    print(f"  reassessment beyond {REASSESSMENT_SLA_DAYS}d         : "
          f"{len(report['reassessment_beyond_30d'])}")
    print("  by risk tier                   : "
          + ", ".join(f"{k}={v}" for k, v in sorted(report["by_risk_tier"].items())))
    if report["worst_offenders"]:
        print("  worst first-verdict offenders  :")
        for w in report["worst_offenders"]:
            print(f"    {w['server_id']}  +{w['hours_over']}h over")
    else:
        print("  worst first-verdict offenders  : none")


def main() -> int:
    """CLI wrapper: read the live registry, audit the SLAs, print the summary."""
    parser = argparse.ArgumentParser(description="Audit PRODUCT_SPEC section 4 freshness SLAs.")
    parser.add_argument("--json", action="store_true", help="also print the raw report as JSON")
    args = parser.parse_args()

    try:
        rows = _read_registry_rows()
    except Exception as exc:  # a failed read is a finding, not a silent empty report
        print(f"ERROR: could not read mcp_server_registry: {exc}")
        return 1

    report = build_freshness_sla_breach_report(rows)
    _print_summary(report)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    # CI-safe self-test: the pure function over known-good synthetic rows.
    from datetime import timedelta

    _now = datetime(2026, 10, 25, 0, 0, 0)
    _rows = [
        # fresh: verdict 2h after first_seen
        {"server_id": "srv-fresh", "first_seen": _now - timedelta(hours=2),
         "last_assessed": _now - timedelta(hours=2), "risk_tier": "LOW", "verdict": "SAFE"},
        # late: verdict 30h after first_seen (beyond the 24h SLA)
        {"server_id": "srv-late", "first_seen": _now - timedelta(hours=32),
         "last_assessed": _now - timedelta(hours=2), "risk_tier": "HIGH", "verdict": "RISKY"},
        # stale: last assessed 45 days ago (beyond the 30d SLA)
        {"server_id": "srv-stale", "first_seen": _now - timedelta(days=45),
         "last_assessed": _now - timedelta(days=45), "risk_tier": "MEDIUM", "verdict": "RISKY"},
    ]
    _report = build_freshness_sla_breach_report(_rows, now=_now)
    assert _report["total"] == 3, _report
    assert len(_report["first_verdict_beyond_24h"]) == 1, _report
    assert len(_report["reassessment_beyond_30d"]) == 1, _report
    assert _report["first_verdict_beyond_24h"][0]["server_id"] == "srv-late", _report
    assert _report["reassessment_beyond_30d"][0]["server_id"] == "srv-stale", _report
    assert _report["missing_first_verdict"] == [], _report
    assert _report["missing_last_assessment"] == [], _report
    assert _report["by_risk_tier"] == {"LOW": 1, "HIGH": 1, "MEDIUM": 1}, _report
    assert _report["worst_offenders"][0]["server_id"] == "srv-late", _report

    # edge case: no verdict and no assessment -> reported as missing, never as a lag
    _edge = build_freshness_sla_breach_report(
        [{"server_id": "srv-empty", "first_seen": (_now - timedelta(days=3)).isoformat(),
          "last_assessed": None, "risk_tier": None, "verdict": None}], now=_now)
    assert _edge["missing_first_verdict"] == ["srv-empty"], _edge
    assert _edge["missing_last_assessment"] == ["srv-empty"], _edge
    assert _edge["first_verdict_beyond_24h"] == [], _edge
    assert _edge["reassessment_beyond_30d"] == [], _edge
    assert _edge["by_risk_tier"] == {"UNKNOWN": 1}, _edge

    if "--live" in __import__("sys").argv:
        raise SystemExit(main())
    print("PASS")
