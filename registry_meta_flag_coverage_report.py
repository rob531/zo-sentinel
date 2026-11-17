"""registry_meta_flag_coverage_report.py -- fleet-wide audit of mcp_server_registry
metadata flags: which flags appear, how often each is populated, and how their
presence correlates with risk tier.

Pure-stdlib report core (summarize_flags / build_report) + a CLI main() that reads
the REAL app table (mcp_server_registry) through app.db / app.models and prints the
report as JSON to stdout. Read-only: no DB writes, no network.

Column note: the registry metadata column is named `metadata` in the DB but is
exposed as `meta` in the report's row contract.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry

UNKNOWN_TIER = "UNKNOWN"


# ---------------------------------------------------------------- pure helpers

def _parse_meta(value: Any) -> Dict[str, Any]:
    """Normalise a meta value into a dict. None / unparsable / non-dict -> {}."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
        except (ValueError, TypeError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _is_scalar(value: Any) -> bool:
    """Only scalar flag values count (dict/list flag payloads are ignored)."""
    return value is None or isinstance(value, (str, int, float, bool))


def _tier_of(row: Dict[str, Any]) -> str:
    tier = row.get("risk_tier")
    if tier is None:
        return UNKNOWN_TIER
    tier = str(tier).strip()
    return tier or UNKNOWN_TIER


def summarize_flags(rows: List[dict]) -> Dict[str, Dict[str, Any]]:
    """Flag-name -> {present_count, pct_of_servers, correlated_tiers}.

    Pure: does not mutate the input rows. `pct_of_servers` is relative to the
    number of rows given (the whole fleet in the report).
    """
    rows = list(rows or [])
    total = len(rows)
    present: Counter = Counter()
    tiers: Dict[str, Counter] = defaultdict(Counter)

    for row in rows:
        if not isinstance(row, dict):
            continue
        meta = _parse_meta(row.get("meta"))
        for flag, value in meta.items():
            if not _is_scalar(value):
                continue
            present[str(flag)] += 1
            tiers[str(flag)][_tier_of(row)] += 1

    out: Dict[str, Dict[str, Any]] = {}
    for flag, count in present.items():
        pct = round(count * 100.0 / total, 1) if total else 0.0
        out[flag] = {
            "present_count": count,
            "pct_of_servers": pct,
            "correlated_tiers": dict(sorted(tiers[flag].items())),
        }
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["present_count"], kv[0])))


def build_report(rows: List[dict]) -> Dict[str, Any]:
    """Full report envelope over the given registry rows."""
    rows = list(rows or [])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": len(rows),
        "flags": summarize_flags(rows),
    }


# ------------------------------------------------------------- real data layer

def load_registry_rows(db, limit: Optional[int] = None) -> List[dict]:
    """Read mcp_server_registry via the app session -> rows with server_id,
    risk_tier and meta (the `metadata` column, raw string or NULL)."""
    stmt = select(McpServerRegistry.server_id, McpServerRegistry.risk_tier,
                  McpServerRegistry.meta)
    if limit:
        stmt = stmt.limit(int(limit))
    return [{"server_id": sid, "risk_tier": tier, "meta": meta}
            for sid, tier, meta in db.execute(stmt).all()]


# ----------------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    """Read the real registry and print the coverage report as JSON to stdout."""
    ap = argparse.ArgumentParser(description="Registry metadata flag coverage report")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of registry rows")
    args = ap.parse_args(argv)

    gen = get_session()
    db = next(gen)
    try:
        rows = load_registry_rows(db, args.limit or None)
    finally:
        try:
            gen.close()
        except Exception:
            pass

    report = build_report(rows)
    print(json.dumps(report, indent=2, sort_keys=False))
    return report


if __name__ == "__main__":
    if "--report" in sys.argv[1:]:
        main([a for a in sys.argv[1:] if a != "--report"])
    else:
        # CI-safe self-test: pure report core over known-good synthetic rows.
        rows = [
            {"server_id": "s1", "risk_tier": "HIGH", "meta": {"trusted": True, "provenance": "gh"}},
            {"server_id": "s2", "risk_tier": "LOW", "meta": {"provenance": "local"}},
            {"server_id": "s3", "risk_tier": None, "meta": None},
        ]
        frozen = json.dumps(rows, sort_keys=True)
        summary = summarize_flags(rows)
        assert summary["provenance"]["present_count"] == 2, summary
        assert summary["trusted"]["present_count"] == 1, summary
        assert summary["provenance"]["pct_of_servers"] == 66.7, summary
        assert summary["trusted"]["pct_of_servers"] == 33.3, summary
        assert summary["provenance"]["correlated_tiers"] == {"HIGH": 1, "LOW": 1}, summary
        assert json.dumps(rows, sort_keys=True) == frozen, "input rows were mutated"

        report = build_report(rows)
        assert report["total_servers"] == 3, report
        assert report["generated_at"].endswith("+00:00"), report
        assert list(report["flags"]) == ["provenance", "trusted"], report

        # edge cases: meta as a JSON string, non-scalar flag values ignored.
        edge = summarize_flags([
            {"server_id": "s4", "risk_tier": "HIGH", "meta": '{"provenance": "gh", "nested": {"a": 1}}'},
            {"server_id": "s5", "risk_tier": "HIGH", "meta": "not-json"},
        ])
        assert edge["provenance"]["present_count"] == 1, edge
        assert "nested" not in edge, edge
        assert edge["provenance"]["correlated_tiers"] == {"HIGH": 1}, edge

        print("PASS")
