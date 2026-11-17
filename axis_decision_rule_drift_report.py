# deps: sqlalchemy
"""axis_decision_rule_drift_report.py -- audits decision-rule / model-version drift
across the LLM student-model axis scores.

Drift = the same axis was scored under more than one decision_rule_version (or
model_version). Rows produced by a superseded rule set are "stale" and must be
rescored before their labels can be compared with the current corpus.

Two layers:
  * pure layer -- build_report(rows, days) takes axis-score rows and touches
    nothing else (stdlib only: no network, no file writes, no DB).
  * data seam -- load_drift_rows(db, days) reads the REAL app table through the
    REAL app session dependency (app.db.get_session) and
    app.models.McpLlmAxisScore, with parameterised, Postgres-portable SQL.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

UNKNOWN_VERSION = "unknown"
MIN_DAYS = 1
MAX_DAYS = 365


# ---------------------------------------------------------------- helpers

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp_days(days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = MIN_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, value))


def _version(value: Any) -> str:
    text = str(value).strip() if value is not None else ""
    return text or UNKNOWN_VERSION


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _row_time(row: Dict[str, Any]) -> datetime:
    dt = _as_utc(row.get("scored_at")) or datetime.min.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------- pure layer

def build_report(rows: List[Dict[str, Any]], days: int = 30) -> Dict[str, Any]:
    """Decision-rule / model-version drift report over axis-score rows.

    rows: dicts with keys axis_name, server_id, decision_rule_version,
    model_version, scored_at.

    Returns {"generated_at", "days", "total_rows", "by_axis",
    "mixed_rule_versions", "mixed_model_versions", "stale_rows",
    "stale_row_ratio"}. Pure: no DB, no network, no writes.
    """
    window = _clamp_days(days)

    by_axis_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if not axis:
            continue
        by_axis_rows[axis].append(row)

    by_axis: Dict[str, Dict[str, Any]] = {}
    mixed_rule_versions: List[str] = []
    mixed_model_versions: List[str] = []
    stale_rows = 0

    for axis_name in sorted(by_axis_rows):
        axis_rows = by_axis_rows[axis_name]

        rule_counts: Dict[str, int] = defaultdict(int)
        model_counts: Dict[str, int] = defaultdict(int)
        servers = set()
        newest = None

        for row in axis_rows:
            rule = _version(row.get("decision_rule_version"))
            model = _version(row.get("model_version"))
            rule_counts[rule] += 1
            model_counts[model] += 1
            server_id = row.get("server_id")
            if server_id is not None:
                servers.add(str(server_id))
            ts = _as_utc(row.get("scored_at"))
            if ts is not None and (newest is None or ts > newest):
                newest = ts

        # most common rule version for the axis; ties -> the version whose newest
        # row is the most recent, then lexicographic (deterministic).
        rule_last_seen: Dict[str, datetime] = {}
        for row in axis_rows:
            rule = _version(row.get("decision_rule_version"))
            ts = _row_time(row)
            if rule not in rule_last_seen or ts > rule_last_seen[rule]:
                rule_last_seen[rule] = ts

        if rule_counts:
            current_rule = sorted(
                rule_counts,
                key=lambda v: (-rule_counts[v], -rule_last_seen[v].timestamp(), v),
            )[0]
            stale_rows += sum(c for v, c in rule_counts.items() if v != current_rule)

        if len(rule_counts) > 1:
            mixed_rule_versions.append(axis_name)
        if len(model_counts) > 1:
            mixed_model_versions.append(axis_name)

        by_axis[axis_name] = {
            "rule_versions": dict(sorted(rule_counts.items())),
            "model_versions": dict(sorted(model_counts.items())),
            "distinct_servers": len(servers),
            "newest_scored_at": _iso(newest) if newest is not None else None,
        }

    total_rows = sum(len(v) for v in by_axis_rows.values())
    ratio = round(stale_rows / total_rows, 4) if total_rows else 0.0

    return {
        "generated_at": _iso_now(),
        "days": window,
        "total_rows": total_rows,
        "by_axis": by_axis,
        "mixed_rule_versions": sorted(mixed_rule_versions),
        "mixed_model_versions": sorted(mixed_model_versions),
        "stale_rows": stale_rows,
        "stale_row_ratio": ratio,
    }


# --------------------------------------------------------------- data seam

def load_drift_rows(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Read the REAL axis scores (windowed) and return them as report rows."""
    window = _clamp_days(days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window)
    stmt = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.scored_at,
    ).where(McpLlmAxisScore.scored_at >= cutoff)
    return [
        {
            "axis_name": axis,
            "server_id": server_id,
            "decision_rule_version": rule,
            "model_version": model,
            "scored_at": scored_at.isoformat() if hasattr(scored_at, "isoformat")
            else scored_at,
        }
        for axis, server_id, rule, model, scored_at in db.execute(stmt).all()
    ]


def build_axis_decision_rule_drift_report(days: int = 30,
                                          db: Optional[Session] = None) -> Dict[str, Any]:
    """Public entry point: the drift report for the last `days` days.

    Uses the supplied session when given, otherwise opens the REAL app session
    (app.db.get_session). Read-only: no writes, no network.
    """
    window = _clamp_days(days)
    if db is not None:
        return build_report(load_drift_rows(db, window), window)
    gen = get_session()
    session = next(gen)
    try:
        return build_report(load_drift_rows(session, window), window)
    finally:
        gen.close()


def format_text_report(report: Dict[str, Any]) -> str:
    """Render the drift report as plain text."""
    rep = report or {}
    lines = ["Axis decision-rule / model-version drift", "=" * 42]
    lines.append(f"generated at   : {rep.get('generated_at')}")
    lines.append(f"window (days)  : {rep.get('days')}")
    lines.append(f"rows audited   : {rep.get('total_rows', 0)}")
    lines.append(f"stale rows     : {rep.get('stale_rows', 0)}")
    lines.append(f"stale ratio    : {rep.get('stale_row_ratio', 0.0)}")
    lines.append(f"mixed rules    : {', '.join(rep.get('mixed_rule_versions') or []) or '(none)'}")
    lines.append(f"mixed models   : {', '.join(rep.get('mixed_model_versions') or []) or '(none)'}")
    lines.append("")
    for axis_name, info in (rep.get("by_axis") or {}).items():
        lines.append(f"[{axis_name}] servers={info.get('distinct_servers', 0)} "
                     f"newest={info.get('newest_scored_at')}")
        for version, count in (info.get("rule_versions") or {}).items():
            lines.append(f"    rule  {version}: {count}")
        for version, count in (info.get("model_versions") or {}).items():
            lines.append(f"    model {version}: {count}")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------- CLI

def _self_test() -> int:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 4 rows, 2 axes, 2 decision_rule_versions; distinct server_id per row
        # so UNIQUE(server_id, axis_name, model_version) is respected.
        s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                              label="HIGH", model_version="v3.0_40974559",
                              decision_rule_version="rules-v1",
                              scored_at=now - timedelta(days=1)))
        s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="overall_risk",
                              label="LOW", model_version="v3.0_40974559",
                              decision_rule_version="rules-v2",
                              scored_at=now - timedelta(days=10)))
        s.add(McpLlmAxisScore(id=3, server_id="srv3", axis_name="auth_strength",
                              label="STRONG", model_version="v3.0_40974559",
                              decision_rule_version="rules-v1",
                              scored_at=now - timedelta(days=2)))
        s.add(McpLlmAxisScore(id=4, server_id="srv4", axis_name="auth_strength",
                              label="WEAK", model_version="v3.0_40974559",
                              decision_rule_version="rules-v2",
                              scored_at=now - timedelta(days=11)))
        s.commit()

        report = build_axis_decision_rule_drift_report(days=30, db=s)
        assert report["total_rows"] == 4, report
        assert report["days"] == 30, report
        assert "overall_risk" in report["mixed_rule_versions"], report
        assert report["stale_rows"] == 2, report
        assert report["stale_row_ratio"] == 0.5, report
        assert set(report["by_axis"]) == {"overall_risk", "auth_strength"}, report
        assert report["by_axis"]["overall_risk"]["distinct_servers"] == 2, report
        assert report["by_axis"]["overall_risk"]["rule_versions"] == \
            {"rules-v1": 1, "rules-v2": 1}, report
        assert report["by_axis"]["overall_risk"]["newest_scored_at"], report
        json.dumps(report)

        # narrow window -> only the fresh rows, no mixed rule versions
        narrow = build_axis_decision_rule_drift_report(days=5, db=s)
        assert narrow["total_rows"] == 2, narrow
        assert narrow["stale_rows"] == 0, narrow
        assert narrow["stale_row_ratio"] == 0.0, narrow
        assert narrow["mixed_rule_versions"] == [], narrow

        # days clamped to 1..365
        assert _clamp_days(0) == 1
        assert _clamp_days(1000) == 365
        assert _clamp_days("abc") == 1
        assert build_axis_decision_rule_drift_report(days=9999, db=s)["days"] == 365
        assert "Axis decision-rule" in format_text_report(report)
    finally:
        s.close()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Axis decision-rule / model-version drift report")
    ap.add_argument("--days", type=int, default=30, help="window size in days (1..365)")
    ap.add_argument("--live", action="store_true",
                    help="read the real mcp_llm_axis_scores via app.db.get_session")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--self-test", action="store_true", help="run the built-in self-test")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    if args.live:
        report = build_axis_decision_rule_drift_report(days=args.days)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_text_report(report))
        return 0

    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
