# deps: sqlalchemy
"""axis_confidence_calibration_report.py -- offline calibration audit for the
LLM axis probability outputs stored in `mcp_llm_axis_scores`.

Two layers (mirrors axis_probability_calibration_report.py):
  * pure layer -- mean(values), bucket(value), build_report(rows) take the rows
    from the caller and touch nothing else (no network, no file writes, no DB).
  * data seam  -- load_axis_rows(db) reads the REAL app table through the REAL
    app session dependency (app.db.get_session) and app.models.McpLlmAxisScore,
    returning rows in the shape build_report() expects.

The module never imports duckdb, never opens its own DB connection and never
writes anything.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

BUCKETS = ("0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")


# ---------------------------------------------------------------- pure layer

def mean(values: List[float]) -> float:
    """Arithmetic mean of a list of floats; 0.0 for an empty/invalid input."""
    nums: List[float] = []
    for v in values or []:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN
            continue
        nums.append(f)
    if not nums:
        return 0.0
    return sum(nums) / len(nums)


def bucket(value: float) -> str:
    """Map a probability in [0, 1] onto its confidence bucket label."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if v != v:  # NaN
        v = 0.0
    v = min(1.0, max(0.0, v))
    if v < 0.2:
        return BUCKETS[0]
    if v < 0.4:
        return BUCKETS[1]
    if v < 0.6:
        return BUCKETS[2]
    if v < 0.8:
        return BUCKETS[3]
    return BUCKETS[4]


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _row_confidence(row: Dict[str, Any]) -> float:
    """Certainty of one axis row in [0, 1].

    With more than one probability the confidence is the normalised certainty
    1 - H/log2(k); with only p_top known the model's own top probability is
    the best available confidence.
    """
    weights = [w for w in (_as_float(row.get("p_top")),
                           _as_float(row.get("p_critical")),
                           _as_float(row.get("p_danger")))
               if w is not None and w > 0.0]
    if not weights:
        return 0.0
    if len(weights) == 1:
        return min(1.0, weights[0])
    total = sum(weights)
    if total <= 0.0:
        return 0.0
    probs = [w / total for w in weights]
    entropy = -sum(p * math.log2(p) for p in probs)
    conf = 1.0 - entropy / math.log2(len(probs))
    return min(1.0, max(0.0, conf))


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the per-axis confidence-calibration report from caller-supplied rows.

    Rows without a usable p_top (or without an axis_name) are ignored -- never
    an error. Every division is guarded against a zero denominator.
    """
    per_axis: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if not axis:
            continue
        p_top = _as_float(row.get("p_top"))
        if p_top is None:
            continue
        per_axis.setdefault(axis, []).append({
            "server_id": row.get("server_id"),
            "p_top": min(1.0, max(0.0, p_top)),
            "escalated": bool(row.get("escalated")),
            "decision_rule_version": str(row.get("decision_rule_version") or "unknown"),
            "confidence": _row_confidence(row),
        })

    axes_out: List[Dict[str, Any]] = []
    all_servers = set()
    escalated_servers = set()

    for axis in sorted(per_axis):
        items = per_axis[axis]
        samples = len(items)
        counts = {b: 0 for b in BUCKETS}
        versions: Dict[str, int] = {}
        escalated = 0
        for it in items:
            counts[bucket(it["p_top"])] += 1
            ver = it["decision_rule_version"]
            versions[ver] = versions.get(ver, 0) + 1
            if it["escalated"]:
                escalated += 1
            sid = it["server_id"]
            if sid is not None:
                all_servers.add(sid)
                if it["escalated"]:
                    escalated_servers.add(sid)
        axes_out.append({
            "axis_name": axis,
            "samples": samples,
            "mean_p_top": round(mean([it["p_top"] for it in items]), 6),
            "mean_confidence": round(mean([it["confidence"] for it in items]), 6),
            "escalated": escalated,
            "escalation_rate": round(escalated / samples, 6) if samples else 0.0,
            "buckets": counts,
            "decision_rule_versions": versions,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axis_count": len(axes_out),
        "server_count": len(all_servers),
        "axes": axes_out,
        "escalated_server_count": len(escalated_servers),
    }


# --------------------------------------------------------------- data seam

def load_axis_rows(db: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as build_report() rows."""
    stmt = select(McpLlmAxisScore).where(McpLlmAxisScore.p_top.isnot(None))
    if model_version:
        stmt = stmt.where(McpLlmAxisScore.model_version == model_version)
    out: List[Dict[str, Any]] = []
    for r in db.execute(stmt).scalars().all():
        out.append({
            "server_id": r.server_id,
            "axis_name": r.axis_name,
            "label": r.label,
            "p_top": r.p_top,
            "p_critical": r.p_critical,
            "p_danger": r.p_danger,
            "escalated": bool(r.escalated),
            "decision_rule_version": r.decision_rule_version,
            "scored_at": r.scored_at.isoformat() if r.scored_at else None,
        })
    return out


def main(db: Optional[Session] = None, model_version: Optional[str] = None) -> Dict[str, Any]:
    """Generate the report payload from the REAL app table."""
    if db is not None:
        return build_report(load_axis_rows(db, model_version=model_version))
    gen = get_session()
    session = next(gen)
    try:
        return build_report(load_axis_rows(session, model_version=model_version))
    finally:
        gen.close()


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    synthetic = [
        {"server_id": "srv-1", "axis_name": "overall_risk", "label": "HIGH",
         "p_top": 0.9, "p_critical": 0.05, "p_danger": 0.05, "escalated": True,
         "decision_rule_version": "v3.0", "scored_at": "2026-01-01T00:00:00+00:00"},
        {"server_id": "srv-2", "axis_name": "overall_risk", "label": "LOW",
         "p_top": 0.1, "p_critical": 0.0, "p_danger": 0.0, "escalated": False,
         "decision_rule_version": "v3.0", "scored_at": "2026-01-01T00:00:00+00:00"},
        {"server_id": "srv-3", "axis_name": "auth_strength", "label": "STRONG",
         "p_top": 0.95, "p_critical": 0.03, "p_danger": 0.02, "escalated": False,
         "decision_rule_version": "v3.1", "scored_at": "2026-01-01T00:00:00+00:00"},
        {"server_id": "srv-4", "axis_name": "auth_strength", "label": "WEAK",
         "p_top": 0.5, "p_critical": 0.3, "p_danger": 0.2, "escalated": False,
         "decision_rule_version": "v3.1", "scored_at": "2026-01-01T00:00:00+00:00"},
        {"server_id": "srv-5", "axis_name": "auth_strength", "label": "MEDIUM",
         "p_top": 0.3, "p_critical": 0.2, "p_danger": 0.1, "escalated": False,
         "decision_rule_version": "v3.1", "scored_at": "2026-01-01T00:00:00+00:00"},
    ]
    report = build_report(synthetic)
    assert report["axis_count"] == 2, report
    by_axis = {a["axis_name"]: a for a in report["axes"]}
    assert by_axis["overall_risk"]["escalated"] == 1, report
    assert by_axis["auth_strength"]["escalated"] == 0, report
    for a in report["axes"]:
        assert sum(a["buckets"].values()) == a["samples"], a
        assert set(a["buckets"]) == set(BUCKETS), a
        assert 0.0 <= a["escalation_rate"] <= 1.0, a
        assert 0.0 <= a["mean_p_top"] <= 1.0, a
        assert 0.0 <= a["mean_confidence"] <= 1.0, a
        assert sum(a["decision_rule_versions"].values()) == a["samples"], a
    assert report["server_count"] == 5, report
    assert report["escalated_server_count"] == 1, report
    assert report["generated_at"], report

    # helper edge cases
    assert mean([]) == 0.0
    assert mean([1.0, 3.0]) == 2.0
    assert bucket(0.0) == "0.0-0.2"
    assert bucket(0.2) == "0.2-0.4"
    assert bucket(0.95) == "0.8-1.0"
    assert bucket(1.0) == "0.8-1.0"

    # real data layer wiring: read the real table through the real model/session
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                              label="HIGH", p_top=0.9, p_critical=0.05, p_danger=0.05,
                              escalated=True, decision_rule_version="v3.0",
                              model_version="v3.0_40974559"))
        s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                              label="STRONG", p_top=0.2, escalated=False,
                              decision_rule_version="v3.0",
                              model_version="v3.0_40974559"))
        s.commit()
        rows = load_axis_rows(s)
        assert len(rows) == 2, rows
        rep2 = build_report(rows)
        assert rep2["axis_count"] == 2, rep2
        assert rep2["server_count"] == 1, rep2
        assert rep2["escalated_server_count"] == 1, rep2
    finally:
        s.close()

    # the same rows through a LOCAL FastAPI app with the session dependency overridden
    app = FastAPI()

    @app.get("/report")
    def _report(db: Session = Depends(get_session)):
        return main(db=db)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    resp = client.get("/report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["axis_count"] == 2, resp.text

    print("PASS")
