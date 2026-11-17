#!/usr/bin/env python3
"""registry_first_seen_age_report -- standalone age-cohort report for mcp_server_registry.

Buckets registry servers by age since ``first_seen`` and writes a JSON summary to
stdout. No HTTP route: this is a CLI/report module.

The bucketing logic is pure (stdlib only, no DB, no network). ``run()`` reads the
authoritative APP table ``mcp_server_registry`` through the real SQLAlchemy session
(``app.db``) and the real model (``app.models``) -- never a local engine, never an
in-memory stand-in.

CLI:
    python3 registry_first_seen_age_report.py            # read the live registry
    python3 registry_first_seen_age_report.py --demo     # synthetic corpus, no DB
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal, get_session  # noqa: F401  (real data layer)
from app.models import McpServerRegistry

BUCKETS = ("0-7", "8-30", "31-90", "91-180", "181-365", "365+")
UNKNOWN_BUCKET = "unknown"


def age_bucket(days: int) -> str:
    """Age in days -> fixed bucket label. Negative ages (clock skew) read as new."""
    if days is None:
        return UNKNOWN_BUCKET
    d = int(days)
    if d <= 7:
        return "0-7"
    if d <= 30:
        return "8-30"
    if d <= 90:
        return "31-90"
    if d <= 180:
        return "91-180"
    if d <= 365:
        return "181-365"
    return "365+"


def _parse_first_seen(value) -> "datetime | None":
    """Accept an ISO 8601 string or a datetime; None/invalid -> None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _age_days(first_seen, now: datetime) -> "int | None":
    parsed = _parse_first_seen(first_seen)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds() // 86400)


def build_age_buckets(rows: list) -> dict:
    """Registry rows -> age-bucket summary.

    Each row is a dict with keys ``server_id``, ``first_seen`` (ISO 8601 or None),
    ``registry_source``, ``risk_tier``. Rows with a missing/unparseable
    ``first_seen`` land in the ``unknown`` bucket.
    """
    now = datetime.now(timezone.utc)
    counts = {label: 0 for label in BUCKETS}
    counts[UNKNOWN_BUCKET] = 0
    per_source: dict = {}

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        days = _age_days(row.get("first_seen"), now)
        label = age_bucket(days) if days is not None else UNKNOWN_BUCKET
        counts[label] += 1

        source = row.get("registry_source") or "unknown"
        entry = per_source.setdefault(source, {"count": 0, "oldest_days": 0})
        entry["count"] += 1
        if days is not None and days > entry["oldest_days"]:
            entry["oldest_days"] = days

    total = sum(counts.values())
    buckets = []
    for label in list(BUCKETS) + [UNKNOWN_BUCKET]:
        count = counts[label]
        buckets.append({
            "bucket": label,
            "count": count,
            "pct": round((count * 100.0 / total), 2) if total else 0.0,
        })

    by_source = [
        {"registry_source": source, "count": data["count"], "oldest_days": data["oldest_days"]}
        for source, data in sorted(per_source.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    ]

    return {
        "total": total,
        "buckets": buckets,
        "by_source": by_source,
        "generated_at": now.isoformat(),
    }


def fetch_registry_rows(db) -> list:
    """Read mcp_server_registry via the real session -> row dicts for the report."""
    out = []
    for r in db.query(McpServerRegistry).all():
        first_seen = getattr(r, "first_seen", None)
        out.append({
            "server_id": getattr(r, "server_id", None),
            "first_seen": first_seen.isoformat() if isinstance(first_seen, datetime) else first_seen,
            "registry_source": getattr(r, "registry_source", None),
            "risk_tier": getattr(r, "risk_tier", None),
        })
    return out


def _demo_rows() -> list:
    now = datetime.now(timezone.utc)
    def iso(days_ago: int) -> str:
        return (now - timedelta(days=days_ago)).isoformat()
    return [
        {"server_id": "d1", "first_seen": iso(2), "registry_source": "official", "risk_tier": "LOW"},
        {"server_id": "d2", "first_seen": iso(20), "registry_source": "official", "risk_tier": "MEDIUM"},
        {"server_id": "d3", "first_seen": iso(60), "registry_source": "npm", "risk_tier": "HIGH"},
        {"server_id": "d4", "first_seen": iso(200), "registry_source": "npm", "risk_tier": "HIGH"},
        {"server_id": "d5", "first_seen": iso(400), "registry_source": "github", "risk_tier": "CRITICAL"},
        {"server_id": "d6", "first_seen": None, "registry_source": "github", "risk_tier": None},
    ]


def run(argv=None) -> int:
    """Build the report and print it as JSON. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Registry first_seen age cohort report")
    parser.add_argument("--demo", action="store_true",
                        help="use a synthetic corpus instead of the live registry")
    args = parser.parse_args(argv)

    if args.demo:
        rows = _demo_rows()
    else:
        db = SessionLocal()
        try:
            rows = fetch_registry_rows(db)
        except Exception as exc:
            print(f"[registry_first_seen_age_report] registry read failed: {exc}", file=sys.stderr)
            return 1
        finally:
            db.close()

    print(json.dumps(build_age_buckets(rows), indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":  # self-test: pure logic + CLI path, no DB required
    corpus = [
        {"server_id": "s1", "first_seen": "2026-11-01T00:00:00+00:00",
         "registry_source": "official", "risk_tier": "LOW"},
        {"server_id": "s2", "first_seen": "2026-10-01T00:00:00+00:00",
         "registry_source": "npm", "risk_tier": "MEDIUM"},
        {"server_id": "s3", "first_seen": "2026-08-01T00:00:00+00:00",
         "registry_source": "npm", "risk_tier": "HIGH"},
        {"server_id": "s4", "first_seen": "2026-05-01T00:00:00+00:00",
         "registry_source": "github", "risk_tier": "HIGH"},
        {"server_id": "s5", "first_seen": "2025-06-01T00:00:00+00:00",
         "registry_source": "github", "risk_tier": "CRITICAL"},
        {"server_id": "s6", "first_seen": None,
         "registry_source": "github", "risk_tier": None},
    ]

    report = build_age_buckets(corpus)

    assert report["total"] == len(corpus), report
    assert sum(b["count"] for b in report["buckets"]) == len(corpus), report
    assert age_bucket(400) == "365+", age_bucket(400)
    assert age_bucket(0) == "0-7" and age_bucket(7) == "0-7"
    assert age_bucket(8) == "8-30" and age_bucket(30) == "8-30"
    assert age_bucket(31) == "31-90" and age_bucket(90) == "31-90"
    assert age_bucket(91) == "91-180" and age_bucket(180) == "91-180"
    assert age_bucket(181) == "181-365" and age_bucket(365) == "181-365"
    assert age_bucket(366) == "365+"

    counts = {b["bucket"]: b["count"] for b in report["buckets"]}
    assert counts["unknown"] == 1, report
    assert counts["365+"] == 1, report

    for b in report["buckets"]:
        assert 0.0 <= b["pct"] <= 100.0, b

    sources = {s["registry_source"]: s for s in report["by_source"]}
    assert sources["github"]["count"] == 3, report
    assert sources["github"]["oldest_days"] >= 365, report
    assert sources["npm"]["oldest_days"] >= 60, report

    # invalid first_seen also falls into "unknown"
    bad = build_age_buckets([{"server_id": "x", "first_seen": "not-a-date",
                              "registry_source": "npm", "risk_tier": None}])
    assert {b["bucket"]: b["count"] for b in bad["buckets"]}["unknown"] == 1, bad

    # CLI path (synthetic corpus) prints JSON and exits 0
    assert run(["--demo"]) == 0

    print("PASS")
