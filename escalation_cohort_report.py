"""escalation_cohort_report.py -- escalation cohort report over the LLM axis scores.

Read-only report generator: summarises which (escalated_to, axis_name) cohorts the
scoring model escalated into, how often each axis escalates, and which servers
carry the escalations.

Data access: the REAL app data layer (app.db / app.models). Rows come either from
a caller-supplied query callable (pure/testable) or from a SQLAlchemy Session over
mcp_llm_axis_scores. No writes, no network, no HTTP route.

Aggregation is stdlib-only (collections / statistics / datetime / json); the only
third-party import is SQLAlchemy for the live query.
"""
# deps: sqlalchemy
from __future__ import annotations

import inspect
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

DEFAULT_DAYS = 30
MIN_DAYS = 1
MAX_DAYS = 3650

SCORE_COLUMNS = ("server_id", "axis_name", "label", "p_top", "escalated",
                 "escalated_to", "decision_rule_version", "model_version",
                 "scored_at")

RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _clamp_days(days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(MIN_DAYS, min(MAX_DAYS, value))


def _cutoff(days: int) -> datetime:
    # scored_at is a naive TIMESTAMP column -> compare against naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(value)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_row(row: Any) -> Dict[str, Any]:
    """Row -> dict keyed by SCORE_COLUMNS (dicts, SQLAlchemy rows, ORM objects, tuples)."""
    if isinstance(row, dict):
        out = {c: row.get(c) for c in SCORE_COLUMNS}
        out["url"] = row.get("url")
        out["name"] = row.get("name")
        return out
    if hasattr(row, "_mapping"):
        mapping = dict(row._mapping)
        out = {c: mapping.get(c) for c in SCORE_COLUMNS}
        out["url"] = mapping.get("url")
        out["name"] = mapping.get("name")
        return out
    if hasattr(row, "server_id"):
        out = {c: getattr(row, c, None) for c in SCORE_COLUMNS}
        out["url"] = getattr(row, "url", None)
        out["name"] = getattr(row, "name", None)
        return out
    seq = list(row)
    out = {c: (seq[i] if i < len(seq) else None) for i, c in enumerate(SCORE_COLUMNS)}
    out["url"] = None
    out["name"] = None
    return out


def _materialise(result: Any) -> List[Any]:
    if result is None:
        return []
    if hasattr(result, "all"):
        result = result.all()
    elif hasattr(result, "fetchall"):
        result = result.fetchall()
    if isinstance(result, dict):
        return [result]
    if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
        return list(result)
    return [result]


def _call_query(query_fn: Callable, days: int) -> List[Any]:
    """Call the injected query callable -- with the window size when it accepts one."""
    try:
        sig = inspect.signature(query_fn)
        positional = [p for p in sig.parameters.values()
                      if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if positional:
            return _materialise(query_fn(days))
    except (TypeError, ValueError):
        pass
    return _materialise(query_fn())


def _load_rows_from_session(db: Session, days: int) -> List[Dict[str, Any]]:
    """Read the escalation-relevant score rows from the REAL score table."""
    cutoff = _cutoff(days)
    raw = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.escalated_to,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.scored_at,
            McpServerRegistry.name,
            McpServerRegistry.url,
        )
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()
    return [_normalise_row(r) for r in raw]


def _gather(source: Any, days: int) -> List[Dict[str, Any]]:
    """Resolve the data source into a list of normalised row dicts."""
    if source is None:
        provider = get_session()
        session = next(provider)
        try:
            return _load_rows_from_session(session, days)
        finally:
            session.close()
            try:
                next(provider)
            except StopIteration:
                pass
    if isinstance(source, Session):
        return _load_rows_from_session(source, days)
    if callable(source):
        return [_normalise_row(r) for r in _call_query(source, days)]
    return [_normalise_row(r) for r in _materialise(source)]


def _within_window(row: Dict[str, Any], days: int) -> bool:
    """Rows without a scored_at are kept (synthetic/legacy rows); dated rows are windowed."""
    scored_at = row.get("scored_at")
    if scored_at is None:
        return True
    if isinstance(scored_at, str):
        try:
            scored_at = datetime.fromisoformat(scored_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if scored_at.tzinfo is not None:
            scored_at = scored_at.astimezone(timezone.utc).replace(tzinfo=None)
    return scored_at >= _cutoff(days)


def _tier(value: Any) -> str:
    return str(value).strip().lower() if value not in (None, "") else "unknown"


def _highest_tier(tiers: Iterable[str]) -> str:
    best = "unknown"
    best_rank = -1
    for tier in tiers:
        rank = RISK_ORDER.get(tier.upper(), -1)
        if rank > best_rank:
            best_rank = rank
            best = tier
    return best


def build_report(db_session_or_query_fn: Any = None, days: int = 30) -> Dict[str, Any]:
    """Summarise escalation cohorts.

    db_session_or_query_fn: a SQLAlchemy Session, a callable returning score rows
    (dicts / ORM rows), or an iterable of rows. None -> live app session.
    days: window size in days (1..3650).

    Returns:
        {"days", "total_rows", "escalated_rows", "escalation_rate",
         "cohorts": [{"escalated_to", "axis_name", "count", "servers", "mean_p_top"}],
         "axes": [{"axis_name", "rows", "escalated", "rate"}],
         "servers": {server_id: {"escalated_axes", "escalated_to", "max_p_top"}}}
    """
    window = _clamp_days(days)
    rows = [r for r in _gather(db_session_or_query_fn, window) if _within_window(r, window)]

    total_rows = len(rows)
    escalated_rows = 0
    cohort_counts: Counter = Counter()
    cohort_servers: Dict[Any, set] = defaultdict(set)
    cohort_ptops: Dict[Any, List[float]] = defaultdict(list)
    axis_rows: Counter = Counter()
    axis_escalated: Counter = Counter()
    server_axes: Dict[str, List[str]] = defaultdict(list)
    server_tiers: Dict[str, List[str]] = defaultdict(list)
    server_ptops: Dict[str, List[float]] = defaultdict(list)

    for row in rows:
        server_id = str(row.get("server_id") or "")
        axis_name = str(row.get("axis_name") or "unknown")
        axis_rows[axis_name] += 1
        p_top = _as_float(row.get("p_top"))
        if p_top is not None:
            server_ptops[server_id].append(p_top)
        if not _as_bool(row.get("escalated")):
            continue

        escalated_rows += 1
        axis_escalated[axis_name] += 1
        tier = _tier(row.get("escalated_to"))
        key = (tier, axis_name)
        cohort_counts[key] += 1
        cohort_servers[key].add(server_id)
        if p_top is not None:
            cohort_ptops[key].append(p_top)
        server_axes[server_id].append(axis_name)
        server_tiers[server_id].append(tier)

    cohorts: List[Dict[str, Any]] = []
    for (tier, axis_name), count in cohort_counts.items():
        ptops = cohort_ptops[(tier, axis_name)]
        cohorts.append({
            "escalated_to": tier,
            "axis_name": axis_name,
            "count": int(count),
            "servers": sorted(cohort_servers[(tier, axis_name)]),
            "mean_p_top": float(statistics.fmean(ptops)) if ptops else 0.0,
        })
    cohorts.sort(key=lambda c: (-c["count"], c["escalated_to"], c["axis_name"]))

    axes: List[Dict[str, Any]] = []
    for axis_name in sorted(axis_rows):
        total = axis_rows[axis_name]
        esc = axis_escalated.get(axis_name, 0)
        axes.append({
            "axis_name": axis_name,
            "rows": int(total),
            "escalated": int(esc),
            "rate": (esc / total) if total else 0.0,
        })

    servers: Dict[str, Dict[str, Any]] = {}
    for server_id in sorted(set(server_axes) | set(server_ptops)):
        ptops = server_ptops.get(server_id, [])
        servers[server_id] = {
            "escalated_axes": sorted(set(server_axes.get(server_id, []))),
            "escalated_to": _highest_tier(server_tiers.get(server_id, [])),
            "max_p_top": float(max(ptops)) if ptops else 0.0,
        }

    return {
        "days": window,
        "total_rows": total_rows,
        "escalated_rows": escalated_rows,
        "escalation_rate": (escalated_rows / total_rows) if total_rows else 0.0,
        "cohorts": cohorts,
        "axes": axes,
        "servers": servers,
    }


def render_text(report: Dict[str, Any]) -> str:
    """Render the report dict as aligned plain text."""
    report = report or {}
    lines: List[str] = []
    lines.append("ESCALATION COHORT REPORT")
    lines.append("=" * 60)
    lines.append("days: {}".format(report.get("days", 0)))
    lines.append("total_rows: {}".format(report.get("total_rows", 0)))
    lines.append("escalated_rows: {}".format(report.get("escalated_rows", 0)))
    lines.append("escalation_rate: {:.4f}".format(
        float(report.get("escalation_rate", 0.0) or 0.0)))

    lines.append("")
    lines.append("COHORTS")
    lines.append("{:<14} {:<24} {:>6} {:>12}  {}".format(
        "escalated_to", "axis_name", "count", "mean_p_top", "servers"))
    for cohort in report.get("cohorts", []) or []:
        lines.append("{:<14} {:<24} {:>6} {:>12.4f}  {}".format(
            str(cohort.get("escalated_to", "")), str(cohort.get("axis_name", "")),
            int(cohort.get("count", 0)),
            float(cohort.get("mean_p_top", 0.0) or 0.0),
            ", ".join(cohort.get("servers", []) or [])))

    lines.append("")
    lines.append("AXES")
    lines.append("{:<24} {:>6} {:>10} {:>8}".format(
        "axis_name", "rows", "escalated", "rate"))
    for axis in report.get("axes", []) or []:
        lines.append("{:<24} {:>6} {:>10} {:>8.4f}".format(
            str(axis.get("axis_name", "")), int(axis.get("rows", 0)),
            int(axis.get("escalated", 0)), float(axis.get("rate", 0.0) or 0.0)))

    lines.append("")
    lines.append("SERVERS")
    lines.append("{:<40} {:<16} {:>10}  {}".format(
        "server_id", "escalated_to", "max_p_top", "escalated_axes"))
    for server_id, entry in sorted((report.get("servers", {}) or {}).items()):
        lines.append("{:<40} {:<16} {:>10.4f}  {}".format(
            str(server_id), str(entry.get("escalated_to", "")),
            float(entry.get("max_p_top", 0.0) or 0.0),
            ", ".join(entry.get("escalated_axes", []) or [])))

    return "\n".join(lines)


def main(source: Any = None, days: int = DEFAULT_DAYS) -> int:
    """Build the report from the live app session and print the rendering."""
    report = build_report(source, days)
    print(render_text(report))
    return 0


if __name__ == "__main__":  # CI-safe self-test: pure functions + real models via sqlite
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    def _row(server_id, axis_name, label, p_top, escalated, escalated_to, age_days):
        return {
            "server_id": server_id,
            "axis_name": axis_name,
            "label": label,
            "p_top": p_top,
            "escalated": escalated,
            "escalated_to": escalated_to,
            "decision_rule_version": "drv-1",
            "model_version": "v3.0_40974559",
            "scored_at": (now - timedelta(days=age_days)).isoformat(),
        }

    synthetic = [
        # server A: escalated to 'critical' on overall_risk
        _row("srv-a", "overall_risk", "CRITICAL", 0.93, True, "critical", 1),
        _row("srv-a", "auth_strength", "STRONG", 0.41, False, None, 1),
        # server B: escalated on exploit_surface
        _row("srv-b", "exploit_surface", "HIGH", 0.81, True, "high", 2),
        # server C: not escalated
        _row("srv-c", "overall_risk", "LOW", 0.12, False, None, 3),
    ]

    report = build_report(lambda _days=None: list(synthetic), days=30)
    assert 0.0 <= report["escalation_rate"] <= 1.0, report
    assert report["total_rows"] == 4, report
    assert report["escalated_rows"] == 2, report
    assert abs(report["escalation_rate"] - 0.5) < 1e-9, report
    critical = [c for c in report["cohorts"]
                if c["escalated_to"] == "critical" and c["axis_name"] == "overall_risk"]
    assert len(critical) == 1, report
    assert critical[0]["count"] == 1, report
    assert critical[0]["servers"] == ["srv-a"], report
    assert report["servers"]["srv-a"]["escalated_axes"] == ["overall_risk"], report
    assert report["servers"]["srv-a"]["escalated_to"] == "critical", report
    assert abs(report["servers"]["srv-a"]["max_p_top"] - 0.93) < 1e-9, report

    text = render_text(report)
    assert isinstance(text, str) and text, text
    assert "escalat" in text, text

    # edge case: no rows
    empty = build_report(lambda _days=None: [], days=30)
    assert empty["total_rows"] == 0 and empty["escalation_rate"] == 0.0, empty
    assert empty["cohorts"] == [] and empty["servers"] == {}, empty
    assert "escalat" in render_text(empty), empty

    # REAL data layer: seed the REAL model via sqlite and read rows back
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                             url="https://github.com/stripe/agent-toolkit",
                             registry_source="github"))
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559",
                          p_top=0.88, escalated=True, escalated_to="CRITICAL",
                          scored_at=now - timedelta(days=1)))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                          label="STRONG", model_version="v3.0_40974559",
                          p_top=0.22, escalated=False,
                          scored_at=now - timedelta(days=1)))
    s.commit()
    live = build_report(s, days=30)
    assert live["total_rows"] == 2, live
    assert live["escalated_rows"] == 1, live
    assert live["servers"]["srv1"]["escalated_axes"] == ["overall_risk"], live
    assert live["cohorts"][0]["escalated_to"] == "critical", live
    s.close()

    print("PASS")
