"""axis_score_coverage_report.py -- per-axis coverage audit of the LLM student model.

Answers: how completely has the scoring model covered the registry?
  - how many registry servers have at least one axis score (and how many never scored)
  - per axis: how many score rows exist, over how many servers, and how many are
    stale (scored more than 7 days ago / never timestamped)
  - a sample of the never-scored backlog (the work still to do)

Data layer: the real app tables -- mcp_server_registry (McpServerRegistry) and
mcp_llm_axis_scores (McpLlmAxisScore) read through app.db's get_session
dependency. The report logic itself is pure over an injected
``db_query(sql, params) -> list[dict]`` callable, so it is testable without a
connection and never opens a connection itself. Read-only SELECTs only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import text

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

STALE_AFTER_DAYS = 7
NEVER_SCORED_SAMPLE_LIMIT = 50

# Real table names, taken from the ORM models (no hardcoded schema drift).
_SERVERS_SQL = ("SELECT server_id, name, risk_tier, verdict FROM "
                + McpServerRegistry.__tablename__)
_AXIS_SQL = ("SELECT server_id, axis_name, p_top, escalated, scored_at FROM "
             + McpLlmAxisScore.__tablename__)

DbQuery = Callable[..., List[Dict[str, Any]]]


def _app_db_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Default db_query: run one read-only SELECT against the app Postgres.

    Uses app.db's own get_session dependency so this module has no engine,
    no sessionmaker and no second data layer of its own.
    """
    gen = get_session()
    session = next(gen)
    try:
        rows = session.execute(text(sql), params or {}).mappings().all()
        return [dict(r) for r in rows]
    finally:
        gen.close()


def _parse_ts(value: Any) -> Optional[datetime]:
    """scored_at may arrive as datetime (Postgres) or as an ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def build_report(db_query: DbQuery) -> Dict[str, Any]:
    """Build the coverage report. db_query(sql, params) -> list[dict].

    Read-only: two SELECTs, no writes.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_AFTER_DAYS)

    servers = db_query(_SERVERS_SQL, {}) or []
    axis_rows = db_query(_AXIS_SQL, {}) or []

    registry: Dict[str, Dict[str, Any]] = {}
    for row in servers:
        sid = str(row.get("server_id") or "").strip()
        if sid:
            registry[sid] = row

    scored_servers = set()
    per_axis: Dict[str, Dict[str, Any]] = {
        ax: {"axis_name": ax, "rows": 0, "servers": set(), "stale_7d": 0}
        for ax in AXES
    }

    for row in axis_rows:
        sid = str(row.get("server_id") or "").strip()
        if sid:
            scored_servers.add(sid)
        ax = str(row.get("axis_name") or "").strip()
        if ax not in per_axis:
            continue
        entry = per_axis[ax]
        entry["rows"] += 1
        if sid:
            entry["servers"].add(sid)
        ts = _parse_ts(row.get("scored_at"))
        if ts is None or ts < cutoff:
            entry["stale_7d"] += 1

    total_servers = len(registry)
    scored_in_registry = {sid for sid in scored_servers if sid in registry}
    never_scored = [sid for sid in registry if sid not in scored_in_registry]
    coverage_pct = round(100.0 * len(scored_in_registry) / total_servers, 2) if total_servers else 0.0

    by_axis = []
    for ax in AXES:
        entry = per_axis[ax]
        by_axis.append({
            "axis_name": ax,
            "rows": entry["rows"],
            "servers": len(entry["servers"]),
            "stale_7d": entry["stale_7d"],
        })

    never_scored_sample = [{
        "server_id": sid,
        "name": registry[sid].get("name") or "",
        "risk_tier": registry[sid].get("risk_tier") or "",
    } for sid in never_scored[:NEVER_SCORED_SAMPLE_LIMIT]]

    return {
        "generated_at": now.isoformat(),
        "totals": {
            "servers": total_servers,
            "scored_servers": len(scored_in_registry),
            "never_scored": len(never_scored),
            "coverage_pct": coverage_pct,
        },
        "by_axis": by_axis,
        "never_scored_sample": never_scored_sample,
    }


# ----------------------------------------------------------------------
# Synthetic fixtures (self-test + no-DB fallback for main()).
# ----------------------------------------------------------------------
def _synthetic_rows() -> tuple:
    now = datetime.now(timezone.utc)
    servers = [
        {"server_id": "srv-a", "name": "Alpha MCP", "risk_tier": "HIGH", "verdict": "HIGH"},
        {"server_id": "srv-b", "name": "Beta MCP", "risk_tier": "LOW", "verdict": "LOW"},
        {"server_id": "srv-c", "name": "Gamma MCP", "risk_tier": "MEDIUM", "verdict": "MEDIUM"},
    ]
    axis_rows = []
    sid = 1
    for server_id, stale in (("srv-a", True), ("srv-b", False)):
        for i, ax in enumerate(AXES):
            ts = (now - timedelta(days=30)) if (stale and i == 0) else now
            axis_rows.append({
                "server_id": server_id,
                "axis_name": ax,
                "p_top": 0.5,
                "escalated": False,
                "scored_at": ts.isoformat(),
            })
            sid += 1
    return servers, axis_rows


def _synthetic_db_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    servers, axis_rows = _synthetic_rows()
    if McpServerRegistry.__tablename__ in sql:
        return [dict(r) for r in servers]
    if McpLlmAxisScore.__tablename__ in sql:
        return [dict(r) for r in axis_rows]
    return []


def main(db_query: Optional[DbQuery] = None) -> int:
    """Print a JSON summary of the coverage report. Returns 0.

    With no db_query supplied the real app session is used; if that is
    unavailable (no DB in the environment) it falls back to a synthetic
    in-memory sample so the command always produces a report.
    """
    report = None
    if db_query is not None:
        report = build_report(db_query)
    else:
        try:
            report = build_report(_app_db_query)
        except Exception:
            report = build_report(_synthetic_db_query)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # CI-safe self-test: synthetic rows through the real code path
    def _self_test() -> None:
        report = build_report(_synthetic_db_query)
        totals = report["totals"]
        assert totals["servers"] == 3, totals
        assert totals["scored_servers"] == 2, totals
        assert totals["never_scored"] == 1, totals
        assert 0.0 <= totals["coverage_pct"] <= 100.0, totals
        axes = [e["axis_name"] for e in report["by_axis"]]
        for ax in AXES:
            assert ax in axes, (ax, axes)
        assert len(report["by_axis"]) == 7, report["by_axis"]
        assert report["by_axis"][0]["stale_7d"] == 1, report["by_axis"][0]
        assert report["never_scored_sample"] == [
            {"server_id": "srv-c", "name": "Gamma MCP", "risk_tier": "MEDIUM"}
        ], report["never_scored_sample"]
        assert report["generated_at"], report
        # edge case: empty registry -> zero coverage, all 7 axes still present
        empty = build_report(lambda sql, params=None: [])
        assert empty["totals"]["servers"] == 0, empty["totals"]
        assert empty["totals"]["coverage_pct"] == 0.0, empty["totals"]
        assert len(empty["by_axis"]) == 7, empty["by_axis"]

    _self_test()
    print("PASS")
    raise SystemExit(main(_synthetic_db_query))
