"""threat_intel_aggregator_pulse_rollup_report.py -- pulse-level aggregator rollup.

Rolls up threat_intel_refs by OTX pulse and shows how much of a pulse's
indicator volume comes from aggregator sources (bulk CVE roundups) vs curated
reports. The aggregation itself is a PURE function over plain dicts, so the
report is testable without a database; the live read path pulls the same rows
from the app Postgres (app.db / app.models.ThreatIntelRef).

Output:
    {pulses: [{pulse_id, pulse_name, pulse_created, indicator_count,
               aggregator_count, aggregator_share, indicator_types: [...]}],
     totals: {pulses, indicators, aggregator_indicators, top_pulse}}

Run:  python3 threat_intel_aggregator_pulse_rollup_report.py
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import get_session, SessionLocal
from app.models import ThreatIntelRef

_ROW_FIELDS = ("indicator_type", "indicator_value", "pulse_id", "pulse_name",
               "pulse_created", "is_aggregator", "source")


def _iso(value: Any) -> Any:
    """Datetimes -> ISO 8601 strings; everything else passes through unchanged."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def rollup(rows: List[dict]) -> dict:
    """Pure: rows -> pulse rollup. Deterministic order: indicator_count desc,
    then pulse_id asc. indicator_types within a pulse: count desc, type asc."""
    pulses: Dict[str, Dict[str, Any]] = {}
    total_indicators = 0
    total_aggregator = 0

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pulse_id = str(row.get("pulse_id") or "")
        entry = pulses.get(pulse_id)
        if entry is None:
            entry = {
                "pulse_id": pulse_id,
                "pulse_name": row.get("pulse_name"),
                "pulse_created": _iso(row.get("pulse_created")),
                "indicator_count": 0,
                "aggregator_count": 0,
                "_types": {},
            }
            pulses[pulse_id] = entry

        is_agg = _truthy(row.get("is_aggregator"))
        entry["indicator_count"] += 1
        total_indicators += 1
        if is_agg:
            entry["aggregator_count"] += 1
            total_aggregator += 1
        itype = str(row.get("indicator_type") or "")
        entry["_types"][itype] = entry["_types"].get(itype, 0) + 1

    ordered = sorted(pulses.values(),
                     key=lambda e: (-e["indicator_count"], e["pulse_id"]))

    out_pulses: List[Dict[str, Any]] = []
    for entry in ordered:
        count = entry["indicator_count"]
        out_pulses.append({
            "pulse_id": entry["pulse_id"],
            "pulse_name": entry["pulse_name"],
            "pulse_created": entry["pulse_created"],
            "indicator_count": count,
            "aggregator_count": entry["aggregator_count"],
            "aggregator_share": round(entry["aggregator_count"] / count, 4) if count else 0.0,
            "indicator_types": [
                {"indicator_type": t, "count": c}
                for t, c in sorted(entry["_types"].items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        })

    top_pulse: Optional[str] = out_pulses[0]["pulse_id"] if out_pulses else None

    return {
        "pulses": out_pulses,
        "totals": {
            "pulses": len(out_pulses),
            "indicators": total_indicators,
            "aggregator_indicators": total_aggregator,
            "top_pulse": top_pulse,
        },
    }


def load_rows() -> List[dict]:
    """Live read: threat_intel_refs -> plain dicts in the rollup input shape."""
    session = SessionLocal()
    try:
        records = session.query(ThreatIntelRef).all()
        return [{f: getattr(r, f, None) for f in _ROW_FIELDS} for r in records]
    finally:
        session.close()


def report_from_db() -> dict:
    """Live rollup over the app Postgres threat_intel_refs table."""
    return rollup(load_rows())


def _self_test() -> None:
    created = datetime(2026, 1, 2, 3, 4, 5)
    rows = [
        {"indicator_type": "cve", "indicator_value": "CVE-2026-0001",
         "pulse_id": "p1", "pulse_name": "Pulse One", "pulse_created": created,
         "is_aggregator": False, "source": "otx"},
        {"indicator_type": "cve", "indicator_value": "CVE-2026-0002",
         "pulse_id": "p1", "pulse_name": "Pulse One", "pulse_created": created,
         "is_aggregator": False, "source": "otx"},
        {"indicator_type": "domain", "indicator_value": "evil.example",
         "pulse_id": "p2", "pulse_name": "Known CVE Roundup", "pulse_created": created,
         "is_aggregator": True, "source": "otx"},
    ]
    out = rollup(rows)
    pulses = out["pulses"]
    totals = out["totals"]

    assert len(pulses) == 2, out
    p1 = pulses[0]
    assert p1["pulse_id"] == "p1", out
    assert p1["indicator_count"] == 2, out
    assert p1["aggregator_count"] == 0, out
    assert p1["aggregator_share"] == 0.0, out
    assert p1["indicator_types"] == [{"indicator_type": "cve", "count": 2}], out
    assert p1["pulse_created"] == created.isoformat(), out

    p2 = pulses[1]
    assert p2["pulse_id"] == "p2", out
    assert p2["indicator_count"] == 1, out
    assert p2["aggregator_count"] == 1, out
    assert p2["aggregator_share"] == 1.0, out
    assert p2["indicator_types"] == [{"indicator_type": "domain", "count": 1}], out

    assert totals == {"pulses": 2, "indicators": 3, "aggregator_indicators": 1,
                       "top_pulse": "p1"}, out

    empty = rollup([])
    assert empty["pulses"] == [], empty
    assert empty["totals"] == {"pulses": 0, "indicators": 0, "aggregator_indicators": 0,
                                "top_pulse": None}, empty


def main() -> int:
    try:
        _self_test()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"FAIL: {exc}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
