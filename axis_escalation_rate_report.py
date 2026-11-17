# deps: sqlalchemy
"""axis_escalation_rate_report.py -- escalation-rate report for the LLM axis model.

Measures how often the scoring model escalates each of the 7 risk axes and
which tiers escalations land in, so operators can see calibration drift over
time.

Read-only: no writes to any table, no network calls, no HTTP route. Data comes
from the REAL app data layer (app.db / app.models) via `get_session`.
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

MIN_DAYS = 1
MAX_DAYS = 3650


@contextmanager
def _session_scope() -> Iterator[Session]:
    """Open a session from the REAL app dependency (app.db.get_session)."""
    provider = get_session()
    session = None
    try:
        if hasattr(provider, "__next__"):
            session = next(provider)
        elif hasattr(provider, "__enter__"):
            session = provider.__enter__()
        else:
            session = provider
        yield session
    finally:
        if session is not None and hasattr(session, "close"):
            session.close()
        if provider is not session and hasattr(provider, "close"):
            provider.close()


def _cutoff(days: int) -> datetime:
    # scored_at is a naive TIMESTAMP column -> compare against naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _rate(escalated: int, scored: int) -> float:
    if scored <= 0:
        return 0.0
    return round(escalated / scored, 6)


def _exec_rows(conn: Any, days: int) -> List[Any]:
    """Run the read-only select on whatever session/connection/engine we were given."""
    cutoff = _cutoff(days)
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.escalated_to,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
    ).where(
        McpLlmAxisScore.scored_at >= cutoff,
        McpLlmAxisScore.axis_name.in_(AXES),
    )
    if hasattr(conn, "execute"):
        return list(conn.execute(stmt).all())
    # an Engine was handed in -- open a connection on it
    with conn.connect() as c:
        return list(c.execute(stmt).all())


def _build_report(conn: Any, days: int) -> Dict[str, Any]:
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError("days must be an int")
    if days < MIN_DAYS or days > MAX_DAYS:
        raise ValueError(f"days must be between {MIN_DAYS} and {MAX_DAYS}")

    rows = _exec_rows(conn, days)

    per_axis: Dict[str, Dict[str, Any]] = {}
    for server_id, axis_name, label, escalated, escalated_to, p_top, p_critical, \
            scored_at, decision_rule_version, model_version in rows:
        axis = str(axis_name or "").strip()
        if axis not in AXES:
            continue  # only the 7 legal axes are reportable
        bucket = per_axis.setdefault(axis, {
            "scored": 0,
            "escalated": 0,
            "escalated_to": {},
            "by_model_version": {},
        })
        bucket["scored"] += 1
        mv = str(model_version) if model_version else "unknown"
        bucket["by_model_version"][mv] = bucket["by_model_version"].get(mv, 0) + 1
        if escalated:
            bucket["escalated"] += 1
            tier = str(escalated_to) if escalated_to else "unknown"
            bucket["escalated_to"][tier] = bucket["escalated_to"].get(tier, 0) + 1

    axes: List[Dict[str, Any]] = []
    total_scored = 0
    total_escalated = 0
    for axis in sorted(per_axis):
        bucket = per_axis[axis]
        total_scored += bucket["scored"]
        total_escalated += bucket["escalated"]
        axes.append({
            "axis_name": axis,
            "scored": bucket["scored"],
            "escalated": bucket["escalated"],
            "escalation_rate": _rate(bucket["escalated"], bucket["scored"]),
            "escalated_to": {t: bucket["escalated_to"][t] for t in sorted(bucket["escalated_to"])},
            "by_model_version": {v: bucket["by_model_version"][v]
                                 for v in sorted(bucket["by_model_version"])},
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": int(days),
        "axes": axes,
        "totals": {
            "scored": total_scored,
            "escalated": total_escalated,
            "escalation_rate": _rate(total_escalated, total_scored),
        },
    }


def build_report(conn: Any = None, days: int = 30) -> Dict[str, Any]:
    """Escalation-rate report. `conn` is an optional SQLAlchemy Session/Connection;
    when omitted a session is opened from the REAL app.db.get_session dependency."""
    if conn is not None:
        return _build_report(conn, days)
    with _session_scope() as s:
        return _build_report(s, days)


def main(argv: Optional[List[str]] = None, conn: Any = None) -> int:
    """CLI entry point: print the report as JSON. Returns a process exit code."""
    parser = argparse.ArgumentParser(description="Axis escalation-rate report")
    parser.add_argument("--days", type=int, default=30,
                        help="window in days applied to mcp_llm_axis_scores.scored_at")
    args = parser.parse_args(argv)
    try:
        report = build_report(conn=conn, days=args.days)
    except Exception as exc:
        print(f"axis_escalation_rate_report: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=False, default=str))
    return 0


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    McpLlmAxisScore.__table__.create(bind=eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seed = (
        (1, "srv1", "overall_risk", "HIGH", False, None),
        (2, "srv2", "overall_risk", "CRITICAL", True, "CRITICAL"),
        (3, "srv1", "auth_strength", "STRONG", True, "HIGH"),
        (4, "srv2", "auth_strength", "WEAK", False, None),
    )
    for _id, _sid, _axis, _label, _esc, _to in seed:
        s.add(McpLlmAxisScore(id=_id, server_id=_sid, axis_name=_axis, label=_label,
                              escalated=_esc, escalated_to=_to, scored_at=now,
                              model_version="v3.0_40974559"))
    s.commit()

    app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    report = build_report(conn=s, days=30)
    assert report["days"] == 30, report
    assert len(report["axes"]) == 2, report
    by_name = {a["axis_name"]: a for a in report["axes"]}
    overall = by_name["overall_risk"]
    assert overall["scored"] == 2, overall
    assert overall["escalated"] == 1, overall
    assert overall["escalation_rate"] == 0.5, overall
    assert overall["escalated_to"] == {"CRITICAL": 1}, overall
    assert overall["by_model_version"] == {"v3.0_40974559": 2}, overall
    assert by_name["auth_strength"]["scored"] == 2, by_name
    assert by_name["auth_strength"]["escalated"] == 1, by_name
    assert by_name["auth_strength"]["escalation_rate"] == 0.5, by_name
    assert report["totals"]["scored"] == 4, report
    assert report["totals"]["escalated"] == 2, report
    assert report["totals"]["escalation_rate"] == 0.5, report
    assert report["generated_at"], report

    # edge case: an illegal axis_name is ignored, not reported
    s.add(McpLlmAxisScore(id=5, server_id="srv3", axis_name="not_an_axis",
                          label="HIGH", scored_at=now, model_version="v3.0_40974559"))
    s.commit()
    report2 = build_report(conn=s, days=30)
    assert len(report2["axes"]) == 2, report2
    assert report2["totals"]["scored"] == 4, report2

    # edge case: rows outside the window are excluded
    s.add(McpLlmAxisScore(id=6, server_id="srv3", axis_name="network_egress",
                          label="EXTERNAL", scored_at=now - timedelta(days=90),
                          model_version="v3.0_40974559"))
    s.commit()
    report3 = build_report(conn=s, days=30)
    assert len(report3["axes"]) == 2, report3
    assert report3["totals"]["scored"] == 4, report3

    # edge case: out-of-range windows rejected
    for bad_days in (0, -1, MAX_DAYS + 1):
        try:
            build_report(conn=s, days=bad_days)
        except ValueError:
            pass
        else:
            raise AssertionError(f"days={bad_days} must raise ValueError")

    # main() prints JSON and exits 0
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--days", "30"], conn=s)
    assert rc == 0, rc
    payload = json.loads(buf.getvalue())
    assert payload["totals"]["scored"] == 4, payload

    s.close()
    print("PASS")
