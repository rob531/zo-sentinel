"""axis_score_recalibration_report.py -- recalibration drift report over the 7 risk axes.

Summarises how the LLM risk scores have drifted across recalibration rounds:
per-axis scoring volume, mean top-label probability, critical/escalated counts,
and a per-model_version breakdown, all read from the app's `mcp_llm_axis_scores`
table through the REAL app data layer (app.db / app.models).

Read-only: no writes to the database, no network calls. The row source is a
seam (`db_fetch`) so the report can be produced from a live session, from the
mesh, or from a test fixture.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session  # noqa: F401 -- the real data-layer seam
from app.models import McpLlmAxisScore

# The ONLY axis names this module will report on.
AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

# Portable SQL (works on Postgres and SQLite): no DuckDB-isms.
REPORT_SQL = (
    "SELECT server_id, axis_name, label, p_top, p_critical, escalated, escalated_to, "
    "decision_rule_version, model_version, scored_at "
    "FROM mcp_llm_axis_scores"
)

# Rows injected by a caller (or a test) used when no db_fetch is supplied.
_INJECTED_ROWS: List[dict] = []


def set_injected_rows(rows: Optional[List[dict]]) -> None:
    """Install the row list used when build_recalibration_report() is called
    without a db_fetch. Passing None clears it."""
    global _INJECTED_ROWS
    _INJECTED_ROWS = [r for r in (rows or []) if r is not None]


def _as_dict(row) -> dict:
    if isinstance(row, dict):
        return row
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return dict(mapping)
    try:
        return dict(row)
    except Exception:
        return {}


def _num(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(statistics.fmean(values), 6)


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    return round(statistics.median(values), 6)


def _load_rows(db_fetch: Optional[Callable[[str, list], list]]) -> List[dict]:
    """Rows are fetched through the injected callable; with no callable the
    injected (or empty) row list is used and NO I/O happens."""
    if db_fetch is not None:
        fetched = db_fetch(REPORT_SQL, [])
        return [_as_dict(r) for r in (fetched or [])]
    return [_as_dict(r) for r in _INJECTED_ROWS]


def _summarize(rows: List[dict]) -> dict:
    per_axis: Dict[str, dict] = {
        a: {"axis_name": a, "scored_count": 0, "p_tops": [], "critical_count": 0,
            "escalated_count": 0, "models": {}}
        for a in AXES
    }
    decision_rules: Dict[str, int] = {}
    models_seen = set()

    for row in rows:
        axis = str(row.get("axis_name") or "")
        if axis not in per_axis:
            continue
        bucket = per_axis[axis]
        bucket["scored_count"] += 1

        p_top = _num(row.get("p_top"))
        if p_top is not None:
            bucket["p_tops"].append(p_top)

        label = str(row.get("label") or "").upper()
        escalated_to = str(row.get("escalated_to") or "").upper()
        if label == "CRITICAL" or escalated_to == "CRITICAL":
            bucket["critical_count"] += 1
        if bool(row.get("escalated")):
            bucket["escalated_count"] += 1

        model_version = str(row.get("model_version") or "")
        if model_version:
            models_seen.add(model_version)
            m = bucket["models"].setdefault(
                model_version, {"model_version": model_version, "scored_count": 0, "p_tops": []})
            m["scored_count"] += 1
            if p_top is not None:
                m["p_tops"].append(p_top)

        rule_version = str(row.get("decision_rule_version") or "")
        if rule_version:
            decision_rules[rule_version] = decision_rules.get(rule_version, 0) + 1

    axes_out = []
    for axis in AXES:
        bucket = per_axis[axis]
        if not bucket["scored_count"]:
            continue
        models = [
            {"model_version": mv["model_version"], "scored_count": mv["scored_count"],
             "mean_p_top": _mean(mv["p_tops"])}
            for mv in sorted(bucket["models"].values(), key=lambda m: m["model_version"])
        ]
        axes_out.append({
            "axis_name": axis,
            "scored_count": bucket["scored_count"],
            "mean_p_top": _mean(bucket["p_tops"]),
            "median_p_top": _median(bucket["p_tops"]),
            "critical_count": bucket["critical_count"],
            "escalated_count": bucket["escalated_count"],
            "models": models,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes_out,
        "decision_rules": [
            {"decision_rule_version": rv, "scored_count": cnt}
            for rv, cnt in sorted(decision_rules.items())
        ],
        "model_count": len(models_seen),
    }


def build_recalibration_report(db_fetch: Optional[Callable[[str, list], list]] = None) -> dict:
    """Recalibration drift report over the 7 risk axes.

    db_fetch(sql, params) -> list[dict] is the row seam; when it is None the
    injected row list (see set_injected_rows) is used and no I/O is performed.
    """
    return _summarize(_load_rows(db_fetch))


def sqlalchemy_db_fetch(db: Session) -> Callable[[str, list], List[dict]]:
    """A db_fetch backed by a real app session (Postgres in prod)."""
    def _fetch(sql: str, params: Optional[list] = None) -> List[dict]:
        result = db.execute(text(sql), list(params or []))
        return [dict(r._mapping) for r in result.all()]
    return _fetch


def axis_rows_orm(db: Session) -> List[dict]:
    """The same rows read through the ORM model (McpLlmAxisScore)."""
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.escalated_to,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.scored_at,
    )
    return [dict(r._mapping) for r in db.execute(stmt).all()]


def report_from_session(db: Session) -> dict:
    """Build the report straight from the live app session."""
    return build_recalibration_report(db_fetch=sqlalchemy_db_fetch(db))


if __name__ == "__main__":
    # CI-safe self-test: real app imports, SQLite session via the app data layer.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk", label="HIGH",
                          model_version="v3.0_40974559", p_top=0.8,
                          decision_rule_version="dr-1"))
    s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="overall_risk", label="CRITICAL",
                          model_version="v3.0_40974559", p_top=0.6,
                          decision_rule_version="dr-2", escalated=True))
    s.add(McpLlmAxisScore(id=3, server_id="srv3", axis_name="auth_strength", label="STRONG",
                          model_version="v3.1_00000001", p_top=0.2,
                          decision_rule_version="dr-2"))
    s.commit()

    # Synthetic rows fed through a fake db_fetch (the acceptance path).
    fake_rows = [
        {"server_id": "srv1", "axis_name": "overall_risk", "label": "HIGH", "p_top": 0.8,
         "p_critical": 0.3, "escalated": False, "escalated_to": None,
         "decision_rule_version": "dr-1", "model_version": "v3.0_40974559",
         "scored_at": "2026-10-01T00:00:00+00:00"},
        {"server_id": "srv2", "axis_name": "overall_risk", "label": "CRITICAL", "p_top": 0.6,
         "p_critical": 0.6, "escalated": True, "escalated_to": "CRITICAL",
         "decision_rule_version": "dr-2", "model_version": "v3.0_40974559",
         "scored_at": "2026-10-02T00:00:00+00:00"},
        {"server_id": "srv3", "axis_name": "auth_strength", "label": "STRONG", "p_top": 0.2,
         "p_critical": 0.1, "escalated": False, "escalated_to": None,
         "decision_rule_version": "dr-2", "model_version": "v3.1_00000001",
         "scored_at": "2026-10-03T00:00:00+00:00"},
    ]

    def fake_fetch(sql, params):
        assert "mcp_llm_axis_scores" in sql, sql
        return fake_rows

    rep = build_recalibration_report(db_fetch=fake_fetch)
    assert len(rep["axes"]) == 2, rep
    assert rep["model_count"] == 2, rep
    by_axis = {a["axis_name"]: a for a in rep["axes"]}
    assert abs(by_axis["overall_risk"]["mean_p_top"] - 0.7) < 1e-9, rep
    assert abs(by_axis["auth_strength"]["mean_p_top"] - 0.2) < 1e-9, rep
    assert by_axis["overall_risk"]["scored_count"] == 2, rep
    assert by_axis["overall_risk"]["critical_count"] == 1, rep
    assert by_axis["overall_risk"]["escalated_count"] == 1, rep
    assert len(by_axis["overall_risk"]["models"]) == 1, rep
    assert by_axis["overall_risk"]["models"][0]["model_version"] == "v3.0_40974559", rep
    assert [d["decision_rule_version"] for d in rep["decision_rules"]] == ["dr-1", "dr-2"], rep
    assert rep["decision_rules"][1]["scored_count"] == 2, rep
    assert rep["generated_at"], rep

    # Same report against the real (SQLite) session via both seams.
    live = report_from_session(s)
    live_by_axis = {a["axis_name"]: a for a in live["axes"]}
    assert abs(live_by_axis["overall_risk"]["mean_p_top"] - 0.7) < 1e-9, live
    assert live["model_count"] == 2, live

    orm_report = build_recalibration_report(db_fetch=lambda sql, params: axis_rows_orm(s))
    assert len(orm_report["axes"]) == 2, orm_report
    assert orm_report["model_count"] == 2, orm_report

    # Edge case: no fetchable rows -> zeros, no I/O.
    empty = build_recalibration_report()
    assert empty["axes"] == [], empty
    assert empty["decision_rules"] == [], empty
    assert empty["model_count"] == 0, empty

    # Edge case: an axis outside the allowed 7 is ignored.
    set_injected_rows([{"axis_name": "not_an_axis", "p_top": 1.0, "model_version": "x"}])
    filtered = build_recalibration_report()
    assert filtered["axes"] == [], filtered
    assert filtered["model_count"] == 0, filtered
    set_injected_rows(None)

    print(json.dumps(rep, indent=2, sort_keys=True))
    print("PASS")
