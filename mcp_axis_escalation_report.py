"""mcp_axis_escalation_report.py -- offline audit report of LLM axis escalation activity.

Pure reporting layer over the live score tables: build_escalation_report() and
render_text_report() are stdlib-only pure functions over row dicts (no DB, no
network, no file writes). The optional loader load_escalation_rows() reads the
REAL app data layer (app.db / app.models) so the report can be produced from
the live Postgres score table without inventing a second data store.
"""
# deps: sqlalchemy
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

UNKNOWN = "unknown"

ROW_KEYS = ("server_id", "axis_name", "escalated", "escalated_to", "p_top",
            "model_version", "decision_rule_version")


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "t", "yes", "y")
    return bool(value)


def _as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bucket(row: Dict[str, Any], key: str) -> Dict[str, Any]:
    axis = row.get("axis_name") or UNKNOWN
    entry = {"axis_name": axis, "total": 0, "escalated": 0,
             "escalation_rate": 0.0, "escalated_to": {}}
    if key == "axis":
        return entry
    return entry


def build_escalation_report(rows: List[Dict[str, Any]], top_n: int = 10) -> Dict[str, Any]:
    """Summarise escalation activity over score rows.

    rows: dicts with server_id, axis_name, escalated (bool), escalated_to
    (str|None), p_top (float|None), model_version (str|None),
    decision_rule_version (str|None). Unknown keys are ignored.
    """
    if top_n < 0:
        top_n = 0

    total = 0
    escalated = 0
    by_axis: Dict[str, Dict[str, Any]] = {}
    by_model: Dict[str, Dict[str, Any]] = {}
    by_rule: Dict[str, Dict[str, Any]] = {}
    escalated_rows: List[Dict[str, Any]] = []

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        total += 1
        is_escalated = _as_bool(row.get("escalated"))

        axis = row.get("axis_name") or UNKNOWN
        a = by_axis.setdefault(axis, {"axis_name": axis, "total": 0, "escalated": 0,
                                      "escalation_rate": 0.0, "escalated_to": {}})
        a["total"] += 1

        mv = row.get("model_version") or UNKNOWN
        m = by_model.setdefault(mv, {"model_version": mv, "total": 0, "escalated": 0})
        m["total"] += 1

        drv = row.get("decision_rule_version") or UNKNOWN
        d = by_rule.setdefault(drv, {"decision_rule_version": drv, "total": 0, "escalated": 0})
        d["total"] += 1

        if not is_escalated:
            continue

        escalated += 1
        a["escalated"] += 1
        m["escalated"] += 1
        d["escalated"] += 1
        label = row.get("escalated_to") or UNKNOWN
        a["escalated_to"][label] = a["escalated_to"].get(label, 0) + 1
        escalated_rows.append({
            "server_id": str(row.get("server_id") or ""),
            "axis_name": axis,
            "escalated_to": row.get("escalated_to"),
            "p_top": _as_float(row.get("p_top")),
        })

    for a in by_axis.values():
        a["escalation_rate"] = (a["escalated"] / a["total"]) if a["total"] else 0.0

    escalated_rows.sort(key=lambda r: (-r["p_top"], r["server_id"], r["axis_name"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": total,
        "escalated_rows": escalated,
        "escalation_rate": (escalated / total) if total else 0.0,
        "by_axis": [by_axis[k] for k in sorted(by_axis)],
        "by_model_version": [by_model[k] for k in sorted(by_model)],
        "by_decision_rule_version": [by_rule[k] for k in sorted(by_rule)],
        "top_escalated_servers": escalated_rows[:top_n],
    }


def render_text_report(report: Dict[str, Any]) -> str:
    """Render a report dict as aligned plain text."""
    lines: List[str] = []
    lines.append("MCP Axis Escalation Report")
    lines.append("=" * 60)
    lines.append("generated_at: {}".format(report.get("generated_at", "")))
    lines.append("total_rows: {}".format(report.get("total_rows", 0)))
    lines.append("escalated_rows: {}".format(report.get("escalated_rows", 0)))
    lines.append("escalation_rate: {:.4f}".format(float(report.get("escalation_rate", 0.0) or 0.0)))

    lines.append("")
    lines.append("BY AXIS")
    lines.append("{:<28} {:>8} {:>10} {:>16}  {}".format(
        "axis_name", "total", "escalated", "escalation_rate", "escalated_to"))
    for a in report.get("by_axis", []) or []:
        targets = ", ".join("{}={}".format(k, v)
                            for k, v in sorted((a.get("escalated_to") or {}).items()))
        lines.append("{:<28} {:>8} {:>10} {:>16.4f}  {}".format(
            str(a.get("axis_name", "")), a.get("total", 0), a.get("escalated", 0),
            float(a.get("escalation_rate", 0.0) or 0.0), targets))

    lines.append("")
    lines.append("BY MODEL VERSION")
    lines.append("{:<28} {:>8} {:>10}".format("model_version", "total", "escalated"))
    for m in report.get("by_model_version", []) or []:
        lines.append("{:<28} {:>8} {:>10}".format(
            str(m.get("model_version", "")), m.get("total", 0), m.get("escalated", 0)))

    lines.append("")
    lines.append("BY DECISION RULE VERSION")
    lines.append("{:<28} {:>8} {:>10}".format("decision_rule_version", "total", "escalated"))
    for d in report.get("by_decision_rule_version", []) or []:
        lines.append("{:<28} {:>8} {:>10}".format(
            str(d.get("decision_rule_version", "")), d.get("total", 0), d.get("escalated", 0)))

    lines.append("")
    lines.append("TOP ESCALATED SERVERS")
    lines.append("{:<28} {:<28} {:<14} {:>10}".format(
        "server_id", "axis_name", "escalated_to", "p_top"))
    for s in report.get("top_escalated_servers", []) or []:
        lines.append("{:<28} {:<28} {:<14} {:>10.4f}".format(
            str(s.get("server_id", "")), str(s.get("axis_name", "")),
            str(s.get("escalated_to") or ""), float(s.get("p_top", 0.0) or 0.0)))

    return "\n".join(lines)


def load_escalation_rows(db: Session, model_version: Optional[str] = None,
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read escalation-relevant rows from the REAL score table (app Postgres).

    Returns row dicts shaped for build_escalation_report(); registry metadata is
    joined so the trust-gating override can be applied before any risk label
    leaves the app.
    """
    stmt = select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
                   McpLlmAxisScore.label, McpLlmAxisScore.escalated,
                   McpLlmAxisScore.escalated_to, McpLlmAxisScore.p_top,
                   McpLlmAxisScore.model_version,
                   McpLlmAxisScore.decision_rule_version)
    if model_version:
        stmt = stmt.where(McpLlmAxisScore.model_version == model_version)
    if limit is not None:
        stmt = stmt.limit(int(limit))

    score_rows = db.execute(stmt).all()
    if not score_rows:
        return []

    server_ids = sorted({r[0] for r in score_rows if r[0]})
    registry = {}
    if server_ids:
        for reg in db.execute(
            select(McpServerRegistry.server_id, McpServerRegistry.name,
                   McpServerRegistry.url)
            .where(McpServerRegistry.server_id.in_(server_ids))
        ).all():
            registry[reg[0]] = (reg[1], reg[2])

    out: List[Dict[str, Any]] = []
    for r in score_rows:
        sid, axis, label, esc, esc_to, p_top, mv, drv = r
        name, url = registry.get(sid, (None, None))
        gate = trust_gate(url, name, {"overall_risk": label or ""})
        out.append({
            "server_id": sid,
            "axis_name": axis,
            "escalated": _as_bool(esc),
            "escalated_to": esc_to,
            "p_top": p_top,
            "model_version": mv,
            "decision_rule_version": drv,
            "published_overall_risk": gate.get("published_overall_risk"),
        })
    return out


def build_report_from_db(db: Session, top_n: int = 10,
                         model_version: Optional[str] = None) -> Dict[str, Any]:
    """Convenience: load live rows and build the report."""
    return build_escalation_report(load_escalation_rows(db, model_version=model_version),
                                   top_n=top_n)


if __name__ == "__main__":  # CI-safe self-test: pure functions + real model via sqlite
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    SEED = [
        {"server_id": "srv-a", "axis_name": "overall_risk", "escalated": True,
         "escalated_to": "CRITICAL", "p_top": 0.93,
         "model_version": "v3.0_40974559", "decision_rule_version": "drv-1"},
        {"server_id": "srv-b", "axis_name": "auth_strength", "escalated": True,
         "escalated_to": "HIGH", "p_top": 0.81,
         "model_version": "v3.0_40974559", "decision_rule_version": "drv-2"},
        {"server_id": "srv-c", "axis_name": "overall_risk", "escalated": True,
         "escalated_to": "HIGH", "p_top": 0.42,
         "model_version": "v3.0_40974559", "decision_rule_version": "drv-1"},
        {"server_id": "srv-d", "axis_name": "overall_risk", "escalated": False,
         "escalated_to": None, "p_top": 0.30,
         "model_version": "v3.0_40974559", "decision_rule_version": "drv-1"},
        {"server_id": "srv-e", "axis_name": "auth_strength", "escalated": False,
         "escalated_to": None, "p_top": 0.25,
         "model_version": "v3.0_40974559", "decision_rule_version": "drv-2"},
        {"server_id": "srv-f", "axis_name": "overall_risk", "escalated": False,
         "escalated_to": None, "p_top": 0.10,
         "model_version": "v3.0_40974559", "decision_rule_version": "drv-1"},
        {"server_id": "srv-g", "axis_name": "auth_strength", "escalated": False,
         "escalated_to": None, "p_top": 0.55,
         "model_version": "v3.0_40974560", "decision_rule_version": "drv-2"},
        {"server_id": "srv-h", "axis_name": "auth_strength", "escalated": False,
         "escalated_to": None, "p_top": 0.50,
         "model_version": "v3.0_40974560", "decision_rule_version": "drv-2"},
    ]

    report = build_escalation_report(SEED)
    assert report["total_rows"] == 8, report
    assert report["escalated_rows"] == 3, report
    assert report["escalation_rate"] == 0.375, report
    assert len(report["by_axis"]) == 2, report
    assert sum(a["total"] for a in report["by_axis"]) == 8, report
    assert len(report["by_model_version"]) == 2, report
    assert len(report["by_decision_rule_version"]) == 2, report
    assert set(report.keys()) == {
        "generated_at", "total_rows", "escalated_rows", "escalation_rate",
        "by_axis", "by_model_version", "by_decision_rule_version",
        "top_escalated_servers"}, report
    text = render_text_report(report)
    assert "escalation_rate" in text, text
    assert report["top_escalated_servers"][0]["server_id"] == "srv-a", report

    # edge case: empty input
    empty = build_escalation_report([])
    assert empty["total_rows"] == 0 and empty["escalation_rate"] == 0.0, empty

    # real data layer: seed the REAL model via sqlite and read rows back
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                         poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                             url="https://github.com/stripe/agent-toolkit"))
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559"))
    s.commit()
    live = load_escalation_rows(s, model_version="v3.0_40974559")
    assert len(live) == 1, live
    assert live[0]["server_id"] == "srv1", live
    live_report = build_report_from_db(s)
    assert live_report["total_rows"] == 1, live_report
    s.close()

    print("PASS")
