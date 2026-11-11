"""axis_decision_rule_coverage_report.py -- decision-rule / model-version coverage audit.

Reports how many distinct decision_rule_version + model_version values exist in
mcp_llm_axis_scores, and whether scoring rows are spread across several
versions or pinned to one.

Read-only. The caller supplies the query seam (db_query: callable(sql, params)
-> list[dict]); the real data layer (app.db / app.models) is used by the
supplied adapters, never re-created here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.db import get_session
from app.models import McpLlmAxisScore

AXIS_TABLE = McpLlmAxisScore.__tablename__

GROUPS_SQL = (
    "SELECT decision_rule_version, model_version, "
    "COUNT(*) AS rows, "
    "COUNT(DISTINCT server_id) AS servers, "
    "MIN(scored_at) AS first_scored_at, "
    "MAX(scored_at) AS last_scored_at, "
    "AVG(p_top) AS p_top_avg "
    f"FROM {AXIS_TABLE} "
    "GROUP BY decision_rule_version, model_version"
)

AXES_SQL = (
    "SELECT decision_rule_version, model_version, axis_name, COUNT(*) AS rows "
    f"FROM {AXIS_TABLE} "
    "GROUP BY decision_rule_version, model_version, axis_name"
)

UNKNOWN = "unknown"


# ---------- helpers ---------------------------------------------------------

def _iso(value: Any) -> Optional[str]:
    """Normalise a timestamp to an ISO-8601 string (None stays None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.isoformat()
    return str(value)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _version_key(row: Dict[str, Any]) -> str:
    drv = str(row.get("decision_rule_version") or UNKNOWN)
    mv = str(row.get("model_version") or UNKNOWN)
    return f"{drv}@{mv}"


# ---------- report ----------------------------------------------------------

def build_axis_decision_rule_coverage_report(
    db_query: Callable[[str, List[Any]], List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Audit decision-rule / model-version coverage of the axis score table.

    db_query(sql, params) -> list[dict rows].
    """
    group_rows = db_query(GROUPS_SQL, []) or []
    axis_rows = db_query(AXES_SQL, []) or []

    entries: List[Dict[str, Any]] = []
    for row in group_rows:
        rows = _int(row.get("rows"))
        if rows <= 0:
            continue
        entries.append({
            "decision_rule_version": str(row.get("decision_rule_version") or UNKNOWN),
            "model_version": str(row.get("model_version") or UNKNOWN),
            "rows": rows,
            "servers": _int(row.get("servers")),
            "first_scored_at": _iso(row.get("first_scored_at")),
            "last_scored_at": _iso(row.get("last_scored_at")),
            "p_top_avg": round(_num(row.get("p_top_avg")), 4),
        })
    entries.sort(key=lambda e: (-e["rows"], e["decision_rule_version"], e["model_version"]))

    total_rows = sum(e["rows"] for e in entries)

    axes_by_version: Dict[str, List[str]] = {}
    all_axes = set()
    for row in axis_rows:
        key = _version_key(row)
        axis = str(row.get("axis_name") or "")
        if not axis:
            continue
        all_axes.add(axis)
        axes_by_version.setdefault(key, [])
        if axis not in axes_by_version[key]:
            axes_by_version[key].append(axis)

    axis_names = sorted(all_axes)

    dominant = entries[0] if entries else None
    dominant_key = None
    if dominant is not None:
        dominant_key = f"{dominant['decision_rule_version']}@{dominant['model_version']}"
    dominant_rows = dominant["rows"] if dominant else 0
    dominant_share = round(dominant_rows / total_rows, 4) if total_rows else 0.0

    stale_version_share = 0.0
    if len(entries) > 1 and total_rows:
        stale_version_share = round((total_rows - dominant_rows) / total_rows, 4)

    active_axes = sorted(axes_by_version.get(dominant_key, [])) if dominant_key else []
    if not active_axes:
        active_axes = list(axis_names)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": total_rows,
        "axis_names": axis_names,
        "by_rule_version": entries,
        "dominant_rule_version": dominant["decision_rule_version"] if dominant else None,
        "dominant_model_version": dominant["model_version"] if dominant else None,
        "dominant_share": dominant_share,
        "stale_version_share": stale_version_share,
        "active_axes": active_axes,
    }


def format_report(report: Dict[str, Any]) -> str:
    """Render the report as a plain-text block."""
    lines = [
        "Axis decision-rule coverage report",
        f"generated_at        : {report.get('generated_at')}",
        f"total_rows          : {report.get('total_rows')}",
        f"axis_names          : {', '.join(report.get('axis_names') or []) or '-'}",
        f"active_axes         : {', '.join(report.get('active_axes') or []) or '-'}",
        f"dominant_rule_ver   : {report.get('dominant_rule_version') or '-'}",
        f"dominant_model_ver  : {report.get('dominant_model_version') or '-'}",
        f"dominant_share      : {report.get('dominant_share')}",
        f"stale_version_share : {report.get('stale_version_share')}",
        "by_rule_version:",
    ]
    for e in report.get("by_rule_version") or []:
        lines.append(
            "  - {drv} / {mv}  rows={rows} servers={servers} "
            "p_top_avg={p} first={f} last={l}".format(
                drv=e.get("decision_rule_version"), mv=e.get("model_version"),
                rows=e.get("rows"), servers=e.get("servers"), p=e.get("p_top_avg"),
                f=e.get("first_scored_at"), l=e.get("last_scored_at")))
    if not report.get("by_rule_version"):
        lines.append("  (no scoring rows)")
    return "\n".join(lines)


def main(db_query: Callable[[str, List[Any]], List[Dict[str, Any]]]) -> int:
    """Print the coverage report for the supplied query seam."""
    report = build_axis_decision_rule_coverage_report(db_query)
    print(format_report(report))
    return 0


# ---------- real data-layer adapters ---------------------------------------

def session_db_query(db: Any) -> Callable[[str, List[Any]], List[Dict[str, Any]]]:
    """Query seam backed by a real app.db Session (SQLAlchemy)."""
    from sqlalchemy import text

    def _run(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        result = db.execute(text(sql))
        return [dict(row._mapping) for row in result]

    return _run


def live_db_query() -> Callable[[str, List[Any]], List[Dict[str, Any]]]:
    """Query seam over the live app session (get_session)."""
    gen = get_session()
    db = next(gen)
    try:
        return session_db_query(db)
    except Exception:
        gen.close()
        raise


# ---------- self-test -------------------------------------------------------

if __name__ == "__main__":
    CANNED_GROUPS = [
        {"decision_rule_version": "drv_v3", "model_version": "v3.0_40974559",
         "rows": 90, "servers": 30, "first_scored_at": "2026-01-02T03:04:05+00:00",
         "last_scored_at": "2026-02-02T03:04:05+00:00", "p_top_avg": 0.5},
        {"decision_rule_version": "drv_v2", "model_version": "v2.0_40974558",
         "rows": 5, "servers": 5, "first_scored_at": "2026-01-01T00:00:00+00:00",
         "last_scored_at": "2026-01-01T00:00:00+00:00", "p_top_avg": 0.25},
        {"decision_rule_version": "drv_v1", "model_version": "v1.0_40974557",
         "rows": 5, "servers": 5, "first_scored_at": "2025-12-01T00:00:00+00:00",
         "last_scored_at": "2025-12-01T00:00:00+00:00", "p_top_avg": 0.125},
    ]
    CANNED_AXES = [
        {"decision_rule_version": "drv_v3", "model_version": "v3.0_40974559",
         "axis_name": ax, "rows": 13} for ax in
        ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
         "network_egress", "maintainer_trust", "exploit_surface")
    ] + [
        {"decision_rule_version": "drv_v2", "model_version": "v2.0_40974558",
         "axis_name": "overall_risk", "rows": 5},
        {"decision_rule_version": "drv_v1", "model_version": "v1.0_40974557",
         "axis_name": "overall_risk", "rows": 5},
    ]

    def fake_db_query(sql: str, params: List[Any]) -> List[Dict[str, Any]]:
        if "GROUP BY decision_rule_version, model_version, axis_name" in sql:
            return list(CANNED_AXES)
        return list(CANNED_GROUPS)

    def empty_db_query(sql: str, params: List[Any]) -> List[Dict[str, Any]]:
        return []

    report = build_axis_decision_rule_coverage_report(fake_db_query)

    assert report["total_rows"] == 100, report["total_rows"]
    assert len(report["by_rule_version"]) == 3, report["by_rule_version"]
    assert report["dominant_share"] > 0.8, report["dominant_share"]
    for entry in report["by_rule_version"]:
        assert entry["rows"] > 0, entry
    assert report["dominant_rule_version"] == "drv_v3", report
    assert report["stale_version_share"] > 0.0, report
    assert report["active_axes"], report
    assert report["axis_names"], report
    assert report["generated_at"].endswith("+00:00"), report["generated_at"]
    json.dumps(report)

    empty = build_axis_decision_rule_coverage_report(empty_db_query)
    assert empty["total_rows"] == 0, empty
    assert empty["by_rule_version"] == [], empty
    assert empty["dominant_rule_version"] is None, empty
    assert empty["dominant_share"] == 0.0, empty

    main(fake_db_query)
    print("PASS")
