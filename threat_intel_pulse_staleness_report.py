"""threat_intel_pulse_staleness_report.py -- staleness/structure audit of the
external threat-intel reference corpus (app Postgres table `threat_intel_refs`).

The aggregation is a PURE function over plain dicts (build_report / render_report),
so it is testable without a database; the live read path pulls the same rows from
the app Postgres via app.db.get_session + app.models.ThreatIntelRef.

Output (build_report):
    {total_refs, by_source: [{source, refs, stale_refs, oldest_fetched_at,
                              newest_fetched_at, missing_source_url}],
     stale_threshold_days, stale_refs, aggregator_refs, missing_pulse_name,
     missing_source_url, missing_fetched_at, by_indicator_type: {type: count}}

Run:  python3 threat_intel_pulse_staleness_report.py
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import ThreatIntelRef

STALE_THRESHOLD_DAYS = 90

_ROW_FIELDS = ("id", "indicator_type", "indicator_value", "pulse_id", "pulse_name",
               "pulse_created", "is_aggregator", "source", "source_url", "fetched_at")


def _parse_ts(value: Any) -> Optional[datetime]:
    """Tolerant timestamp parse: datetime, ISO 8601 (trailing 'Z' allowed),
    'YYYY-MM-DD HH:MM:SS'. Never raises; None means 'unknown'."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    dt: Optional[datetime] = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return bool(value)


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def build_report(refs: List[dict], now_iso: str,
                 stale_threshold_days: int = STALE_THRESHOLD_DAYS) -> dict:
    """Pure: ref dicts -> staleness/structure report. now_iso is the reference
    'now'. A ref is stale when now - fetched_at exceeds the threshold. A missing
    or unparseable fetched_at is 'unknown age' -- never counted as stale."""
    now = _parse_ts(now_iso) or datetime.now(timezone.utc)
    threshold = timedelta(days=int(stale_threshold_days))

    total_refs = 0
    stale_refs = 0
    aggregator_refs = 0
    missing_pulse_name = 0
    missing_source_url = 0
    missing_fetched_at = 0

    sources: Dict[str, Dict[str, Any]] = {}
    by_indicator_type: Dict[str, int] = {}

    for row in refs or []:
        if not isinstance(row, dict):
            continue
        total_refs += 1

        itype = str(row.get("indicator_type") or "unknown")
        by_indicator_type[itype] = by_indicator_type.get(itype, 0) + 1

        if _truthy(row.get("is_aggregator")):
            aggregator_refs += 1
        if _blank(row.get("pulse_name")):
            missing_pulse_name += 1

        source = str(row.get("source") or "unknown")
        entry = sources.get(source)
        if entry is None:
            entry = {"source": source, "refs": 0, "stale_refs": 0,
                     "oldest_fetched_at": None, "newest_fetched_at": None,
                     "missing_source_url": 0}
            sources[source] = entry
        entry["refs"] += 1

        if _blank(row.get("source_url")):
            missing_source_url += 1
            entry["missing_source_url"] += 1

        ts = _parse_ts(row.get("fetched_at"))
        if ts is None:
            missing_fetched_at += 1
            continue

        iso = ts.astimezone(timezone.utc).isoformat()
        oldest = _parse_ts(entry["oldest_fetched_at"])
        newest = _parse_ts(entry["newest_fetched_at"])
        if oldest is None or ts < oldest:
            entry["oldest_fetched_at"] = iso
        if newest is None or ts > newest:
            entry["newest_fetched_at"] = iso

        if now - ts > threshold:
            stale_refs += 1
            entry["stale_refs"] += 1

    return {
        "total_refs": total_refs,
        "by_source": [sources[s] for s in sorted(sources)],
        "stale_threshold_days": int(stale_threshold_days),
        "stale_refs": stale_refs,
        "aggregator_refs": aggregator_refs,
        "missing_pulse_name": missing_pulse_name,
        "missing_source_url": missing_source_url,
        "missing_fetched_at": missing_fetched_at,
        "by_indicator_type": {k: by_indicator_type[k] for k in sorted(by_indicator_type)},
    }


def render_report(report: dict) -> str:
    """Pure: report dict -> human-readable text."""
    report = report or {}
    lines: List[str] = []
    lines.append("THREAT INTEL PULSE STALENESS REPORT")
    lines.append("=" * 60)
    lines.append(f"total refs          : {report.get('total_refs', 0)}")
    lines.append(f"stale threshold     : {report.get('stale_threshold_days', STALE_THRESHOLD_DAYS)} days")
    lines.append(f"stale refs          : {report.get('stale_refs', 0)}")
    lines.append(f"aggregator refs     : {report.get('aggregator_refs', 0)}")
    lines.append(f"missing pulse name  : {report.get('missing_pulse_name', 0)}")
    lines.append(f"missing source url  : {report.get('missing_source_url', 0)}")
    lines.append(f"missing fetched_at  : {report.get('missing_fetched_at', 0)}")
    lines.append("")
    lines.append("by source:")
    for entry in report.get("by_source") or []:
        lines.append(
            "  - {source}: refs={refs} stale={stale_refs} missing_source_url={missing_source_url}"
            " oldest={oldest} newest={newest}".format(
                source=entry.get("source"),
                refs=entry.get("refs", 0),
                stale_refs=entry.get("stale_refs", 0),
                missing_source_url=entry.get("missing_source_url", 0),
                oldest=entry.get("oldest_fetched_at") or "-",
                newest=entry.get("newest_fetched_at") or "-"))
    lines.append("")
    lines.append("by indicator type:")
    for itype, count in (report.get("by_indicator_type") or {}).items():
        lines.append(f"  - {itype}: {count}")
    return "\n".join(lines)


def load_refs() -> List[dict]:
    """Live read: threat_intel_refs -> plain dicts in the report input shape."""
    gen = get_session()
    session = next(gen)
    try:
        records = session.query(ThreatIntelRef).all()
        return [{f: getattr(r, f, None) for f in _ROW_FIELDS} for r in records]
    finally:
        gen.close()


def report_from_db(now_iso: Optional[str] = None) -> dict:
    """Live report over the app Postgres threat_intel_refs table."""
    now = now_iso or datetime.now(timezone.utc).isoformat()
    return build_report(load_refs(), now)


def _seed_refs(now: datetime) -> List[dict]:
    """5 synthetic refs: one 400 days old, one fetched_at None, one source_url None."""
    fresh = (now - timedelta(days=2)).isoformat()
    ancient = (now - timedelta(days=400)).isoformat()
    return [
        {"id": 1, "indicator_type": "cve", "indicator_value": "CVE-2026-0001",
         "pulse_id": "p1", "pulse_name": "Fresh Pulse", "pulse_created": None,
         "is_aggregator": False, "source": "otx",
         "source_url": "https://otx.example/p1", "fetched_at": fresh},
        {"id": 2, "indicator_type": "cve", "indicator_value": "CVE-2026-0002",
         "pulse_id": "p2", "pulse_name": "Ancient Pulse", "pulse_created": None,
         "is_aggregator": True, "source": "otx",
         "source_url": "https://otx.example/p2", "fetched_at": ancient},
        {"id": 3, "indicator_type": "domain", "indicator_value": "evil.example",
         "pulse_id": "p3", "pulse_name": None, "pulse_created": None,
         "is_aggregator": False, "source": "otx",
         "source_url": "https://otx.example/p3", "fetched_at": None},
        {"id": 4, "indicator_type": "domain", "indicator_value": "bad.example",
         "pulse_id": "p4", "pulse_name": "Roundup", "pulse_created": None,
         "is_aggregator": True, "source": "otx",
         "source_url": None, "fetched_at": ancient.replace("+00:00", "Z")},
        {"id": 5, "indicator_type": "cve", "indicator_value": "CVE-2026-0005",
         "pulse_id": "p5", "pulse_name": "Second Source", "pulse_created": None,
         "is_aggregator": False, "source": "abuse.ch",
         "source_url": "https://abuse.example/p5", "fetched_at": fresh},
    ]


def _self_test() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    refs = _seed_refs(now)
    report = build_report(refs, now.isoformat())

    assert report["total_refs"] == 5, report
    assert report["stale_refs"] >= 1, report
    assert report["missing_source_url"] >= 1, report
    assert report["missing_pulse_name"] == 1, report
    assert report["missing_fetched_at"] == 1, report
    assert report["aggregator_refs"] == 2, report
    assert report["stale_threshold_days"] == STALE_THRESHOLD_DAYS, report
    assert report["by_indicator_type"] == {"cve": 3, "domain": 2}, report

    sources = {e["source"]: e for e in report["by_source"]}
    assert set(sources) == {"otx", "abuse.ch"}, report
    otx = sources["otx"]
    assert otx["refs"] == 4, report
    assert otx["stale_refs"] == 2, report
    assert otx["missing_source_url"] == 1, report
    assert otx["oldest_fetched_at"] is not None, report
    assert otx["newest_fetched_at"] is not None, report
    assert sources["abuse.ch"]["stale_refs"] == 0, report

    text = render_report(report)
    assert "THREAT" in text, text
    assert "otx" in text, text

    empty = build_report([], now.isoformat())
    assert empty["total_refs"] == 0, empty
    assert empty["by_source"] == [], empty
    assert empty["by_indicator_type"] == {}, empty
    assert "THREAT" in render_report(empty)


def run() -> int:
    """Self-test entry point: 0 on success."""
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
    raise SystemExit(run())
