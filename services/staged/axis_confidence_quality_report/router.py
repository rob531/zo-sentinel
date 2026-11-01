# deps: fastapi, sqlalchemy
"""axis_confidence_quality_report.py -- quality audit of the LLM axis scores.

Pure report core: `build_axis_confidence_quality_report(rows)` turns rows of
`mcp_llm_axis_scores` into per-axis confidence statistics, and
`render_markdown(report)` renders that as a short markdown table.

Data access mirrors verdict_breakdown_api.py: the app-tier tables are read
through the real SQLAlchemy session (`from app.db import get_session`) and the
real model (`from app.models import McpLlmAxisScore`) -- no inline stubs, no
second engine, no in-memory store.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis-quality"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

LOW_CONFIDENCE_THRESHOLD = 0.4


# ============================ pure report core ============================

def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "t")
    return bool(value)


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    text = _as_text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: List[float], pct: float) -> float:
    """Linear-interpolation percentile (numpy-compatible); 0.0 for an empty set."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(k)
    high = math.ceil(k)
    if low == high:
        return float(ordered[int(k)])
    return float(ordered[low] * (high - k) + ordered[high] * (k - low))


def _current_decision_rule_version(rows: List[dict]) -> Optional[str]:
    """The decision_rule_version of the most recently scored row."""
    best: Optional[datetime] = None
    best_rule: Optional[str] = None
    for row in rows:
        rule = _as_text(row.get("decision_rule_version"))
        ts = _as_timestamp(row.get("scored_at"))
        if rule is None:
            continue
        if best is None or ts is None or (best is not None and ts > best):
            best = ts if ts is not None else best
            best_rule = rule
    return best_rule


def build_axis_confidence_quality_report(rows: List[dict]) -> dict:
    """Audit the confidence quality of LLM axis scores.

    `rows` are dicts with keys axis_name, p_top, p_critical, escalated,
    model_version, decision_rule_version, scored_at. Rows whose axis_name is not
    one of the seven stored axes are ignored.
    """
    rows = list(rows or [])
    buckets: Dict[str, List[dict]] = {a: [] for a in AXES}
    versions: List[str] = []
    seen_versions = set()

    for row in rows:
        if not isinstance(row, dict):
            continue
        axis = _as_text(row.get("axis_name"))
        if axis not in buckets:
            continue
        buckets[axis].append(row)
        version = _as_text(row.get("model_version"))
        if version and version not in seen_versions:
            seen_versions.add(version)
            versions.append(version)

    current_rule = _current_decision_rule_version(rows)

    per_axis: Dict[str, dict] = {}
    total_rows = 0
    total_escalated = 0
    total_low_confidence = 0
    for axis in AXES:
        axis_rows = buckets[axis]
        if not axis_rows:
            continue
        p_tops = [p for p in (_as_float(r.get("p_top")) for r in axis_rows) if p is not None]
        escalated = sum(1 for r in axis_rows if _as_bool(r.get("escalated")))
        low_conf = sum(1 for p in p_tops if p < LOW_CONFIDENCE_THRESHOLD)
        per_axis[axis] = {
            "count": len(axis_rows),
            "mean_p_top": round(float(statistics.mean(p_tops)), 6) if p_tops else 0.0,
            "p_top_p50": round(_percentile(p_tops, 50.0), 6),
            "p_top_p90": round(_percentile(p_tops, 90.0), 6),
            "low_confidence_count": low_conf,
            "escalated_count": escalated,
        }
        total_rows += len(axis_rows)
        total_escalated += escalated
        total_low_confidence += low_conf

    model_versions = sorted(versions)

    stale_versions: List[str] = []
    for version in model_versions:
        rules = {
            _as_text(r.get("decision_rule_version"))
            for r in rows
            if _as_text(r.get("model_version")) == version
        }
        rules.discard(None)
        if not rules:
            stale_versions.append(version)
            continue
        if current_rule is None or any(rule != current_rule for rule in rules):
            stale_versions.append(version)
    stale_versions = sorted(stale_versions)

    summary: List[str] = []
    if not per_axis:
        summary.append("No axis scores available -- nothing to audit.")
    else:
        weakest = min(per_axis.items(), key=lambda kv: kv[1]["mean_p_top"])
        strongest = max(per_axis.items(), key=lambda kv: kv[1]["mean_p_top"])
        summary.append(
            f"{total_rows} axis rows across {len(per_axis)} axes; "
            f"mean confidence strongest on {strongest[0]} "
            f"({strongest[1]['mean_p_top']:.3f}), weakest on {weakest[0]} "
            f"({weakest[1]['mean_p_top']:.3f}).")
        summary.append(
            f"{total_low_confidence} rows below the {LOW_CONFIDENCE_THRESHOLD} "
            f"confidence threshold; {total_escalated} escalated rows.")
        if stale_versions:
            summary.append(
                f"Stale model versions (decision rule != {current_rule or 'n/a'}): "
                f"{', '.join(stale_versions)}.")
        else:
            summary.append("No stale model versions detected.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "per_axis": per_axis,
        "model_versions": model_versions,
        "stale_versions": stale_versions,
        "current_decision_rule_version": current_rule,
        "totals": {
            "rows": total_rows,
            "escalated": total_escalated,
            "low_confidence": total_low_confidence,
        },
        "summary": summary,
    }


def render_markdown(report: dict) -> str:
    """Render the report as a short markdown table."""
    report = report or {}
    per_axis = report.get("per_axis") or {}
    lines: List[str] = []
    lines.append("# Axis Confidence Quality Report")
    lines.append("")
    lines.append("| axis | count | mean_p_top | p50 | p90 | low_confidence | escalated |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for axis in AXES:
        stats = per_axis.get(axis)
        if not stats:
            continue
        lines.append(
            "| {axis} | {count} | {mean:.3f} | {p50:.3f} | {p90:.3f} | {low} | {esc} |".format(
                axis=axis,
                count=stats.get("count", 0),
                mean=float(stats.get("mean_p_top") or 0.0),
                p50=float(stats.get("p_top_p50") or 0.0),
                p90=float(stats.get("p_top_p90") or 0.0),
                low=stats.get("low_confidence_count", 0),
                esc=stats.get("escalated_count", 0),
            ))
    if not per_axis:
        lines.append("| _no axis scores_ | 0 | 0.000 | 0.000 | 0.000 | 0 | 0 |")
    lines.append("")
    versions = report.get("model_versions") or []
    stale = report.get("stale_versions") or []
    lines.append(f"- model_versions: {', '.join(versions) if versions else 'none'}")
    lines.append(f"- stale_versions: {', '.join(stale) if stale else 'none'}")
    for sentence in report.get("summary") or []:
        lines.append(f"- {sentence}")
    lines.append("")
    return "\n".join(lines)


# ============================ data access ============================

def load_axis_rows(db: Session, limit: int = 100000) -> List[dict]:
    """Read axis score rows from the app-tier table via the real session."""
    limit = max(1, min(int(limit), 500000))
    stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.escalated,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.scored_at,
        )
        .where(McpLlmAxisScore.axis_name.in_(AXES))
        .limit(limit)
    )
    out: List[dict] = []
    for axis_name, p_top, p_critical, escalated, model_version, rule_version, scored_at in db.execute(stmt).all():
        out.append({
            "axis_name": axis_name,
            "p_top": p_top,
            "p_critical": p_critical,
            "escalated": escalated,
            "model_version": model_version,
            "decision_rule_version": rule_version,
            "scored_at": scored_at.isoformat() if isinstance(scored_at, datetime) else scored_at,
        })
    return out


@router.get("/axis-quality/report")
def axis_confidence_quality_report(db: Session = Depends(get_session)) -> dict:
    """Live confidence-quality audit of the LLM axis scores."""
    rows = load_axis_rows(db)
    report = build_axis_confidence_quality_report(rows)
    return {"report": report, "markdown": render_markdown(report)}


def run() -> None:
    """Entry point: build the report from the live app-tier table and print it."""
    from app.db import SessionLocal

    rows: List[dict] = []
    try:
        db = SessionLocal()
        try:
            rows = load_axis_rows(db)
        finally:
            db.close()
    except Exception as exc:  # no DB available -> report on an empty set
        print(f"[axis-quality] axis rows unavailable: {exc}")
    report = build_axis_confidence_quality_report(rows)
    print(render_markdown(report))


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # --- pure core: 3 known-good row sets ---
    healthy_rows = [
        {"axis_name": axis, "p_top": 0.9, "p_critical": 0.05, "escalated": False,
         "model_version": "v3.0_1", "decision_rule_version": "dr-2",
         "scored_at": "2026-10-01T00:00:00+00:00"}
        for axis in AXES
    ]
    healthy = build_axis_confidence_quality_report(healthy_rows)
    assert len(healthy["per_axis"]) == 7, healthy
    assert healthy["per_axis"]["overall_risk"]["mean_p_top"] == 0.9, healthy
    assert all(v["mean_p_top"] == 0.9 for v in healthy["per_axis"].values()), healthy
    assert healthy["totals"]["escalated"] == 0, healthy
    assert healthy["model_versions"] == ["v3.0_1"], healthy
    assert healthy["stale_versions"] == [], healthy
    md = render_markdown(healthy)
    assert "| overall_risk |" in md and "# Axis Confidence Quality Report" in md, md

    degraded_rows = [
        {"axis_name": "overall_risk", "p_top": 0.1, "p_critical": 0.05, "escalated": True,
         "model_version": "v2.0_9", "decision_rule_version": "dr-1",
         "scored_at": "2026-09-01T00:00:00+00:00"},
        {"axis_name": "overall_risk", "p_top": 0.1, "p_critical": 0.02, "escalated": True,
         "model_version": "v3.0_1", "decision_rule_version": "dr-2",
         "scored_at": "2026-10-01T00:00:00+00:00"},
    ] + [
        {"axis_name": axis, "p_top": 0.1, "p_critical": 0.0, "escalated": False,
         "model_version": "v3.0_1", "decision_rule_version": "dr-2",
         "scored_at": "2026-10-01T00:00:00+00:00"}
        for axis in AXES if axis != "overall_risk"
    ]
    degraded = build_axis_confidence_quality_report(degraded_rows)
    assert degraded["per_axis"]["overall_risk"]["escalated_count"] == 2, degraded
    assert sum(v["escalated_count"] for v in degraded["per_axis"].values()) == 2, degraded
    assert degraded["per_axis"]["overall_risk"]["low_confidence_count"] == 2, degraded
    assert degraded["stale_versions"] == ["v2.0_9"], degraded
    assert degraded["model_versions"] == ["v2.0_9", "v3.0_1"], degraded

    empty = build_axis_confidence_quality_report([])
    assert empty["per_axis"] == {}, empty
    assert empty["model_versions"] == [] and empty["stale_versions"] == [], empty
    assert empty["totals"]["rows"] == 0, empty
    assert render_markdown(empty), empty

    # --- real data layer + endpoint, SQLite via dependency override ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_1"))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                          label="STRONG", model_version="v3.0_1"))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/axis-quality/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["per_axis"]["overall_risk"]["count"] == 1, body
    assert body["report"]["model_versions"] == ["v3.0_1"], body
    assert "| overall_risk |" in body["markdown"], body

    print("PASS")
