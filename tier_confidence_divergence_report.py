# deps: sqlalchemy
"""tier_confidence_divergence_report.py -- audit of disagreement between the SFT
model's risk tier and its stated confidence.

Two layers (mirrors axis_confidence_calibration_report.py):
  * pure layer -- build_report(db_rows) takes the rows from the caller and
    touches nothing else (no DB, no network, no HTTP, no file writes).
  * data seam  -- load_db_rows(db) reads the REAL app tables through the REAL
    app session dependency (app.db.get_session) and app.models
    (McpServerRegistry / McpLlmAxisScore), returning rows in the shape
    build_report() expects.

Purpose: spot servers whose verdict is surfaced as firm (a HIGH/CRITICAL risk
tier) while the model itself only reports LOW confidence -- the signal is the
gap between tier severity and stated confidence.

The module never imports duckdb, never opens its own DB connection and never
mutates anything.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

CONFIDENCE_BANDS = ("0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")
UNKNOWN_BAND = "unknown"
LOW_CONFIDENCE_THRESHOLD = 0.5

TIER_SEVERITY = {
    "CRITICAL": 3.0,
    "HIGH": 2.0,
    "HIGH_RISK": 2.0,
    "MEDIUM": 1.0,
    "LOW": 0.0,
    "NONE": 0.0,
}

OUTPUT_PATH = os.path.join("shared", "outputs", "goose",
                           "tier_confidence_divergence_report.json")


# ---------------------------------------------------------------- pure layer

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


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def confidence_band(value: Any) -> str:
    """Map a confidence in [0, 1] onto its confidence-band label."""
    f = _as_float(value)
    if f is None:
        return UNKNOWN_BAND
    f = min(1.0, max(0.0, f))
    if f < 0.2:
        return CONFIDENCE_BANDS[0]
    if f < 0.4:
        return CONFIDENCE_BANDS[1]
    if f < 0.6:
        return CONFIDENCE_BANDS[2]
    if f < 0.8:
        return CONFIDENCE_BANDS[3]
    return CONFIDENCE_BANDS[4]


def tier_severity(tier: Any) -> float:
    """Severity of a risk tier in [0, 3]; unknown tiers score 0."""
    return TIER_SEVERITY.get(str(tier or "").strip().upper(), 0.0)


def _band_sort_key(band: str) -> int:
    try:
        return CONFIDENCE_BANDS.index(band)
    except ValueError:
        return len(CONFIDENCE_BANDS)


def build_report(db_rows: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build the tier/confidence divergence report from caller-supplied rows.

    db_rows maps a table name to a list of row dicts:
      * "mcp_server_registry"   -> server_id, name, risk_tier, verdict,
                                   confidence, last_assessed
      * "mcp_llm_axis_scores"   -> server_id, axis_name, p_top, p_critical,
                                   p_danger, escalated, scored_at,
                                   decision_rule_version

    Pure: no DB, no network, no file writes. Rows without the fields the report
    needs are tolerated, never an error.
    """
    registry = db_rows.get("mcp_server_registry") or []
    scores = db_rows.get("mcp_llm_axis_scores") or []

    by_server: Dict[str, List[Dict[str, Any]]] = {}
    for row in scores:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("server_id") or "").strip()
        if not sid:
            continue
        by_server.setdefault(sid, []).append(row)

    tiers: Dict[str, int] = {}
    band_counts: Dict[str, Dict[str, int]] = {}
    low_confidence_servers = 0
    divergent: List[Dict[str, Any]] = []
    audited = 0

    for row in registry:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("server_id") or "").strip()
        if not sid:
            continue
        audited += 1

        tier = str(row.get("risk_tier") or "UNKNOWN").strip().upper() or "UNKNOWN"
        tiers[tier] = tiers.get(tier, 0) + 1

        confidence = _as_float(row.get("confidence"))
        band = confidence_band(confidence)
        band_counts.setdefault(tier, {})
        band_counts[tier][band] = band_counts[tier].get(band, 0) + 1
        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            low_confidence_servers += 1

        axis_rows = by_server.get(sid, [])
        p_tops = []
        escalated_axes: List[str] = []
        for a in axis_rows:
            p_top = _as_float(a.get("p_top"))
            if p_top is not None:
                p_tops.append(min(1.0, max(0.0, p_top)))
            if a.get("escalated"):
                axis = str(a.get("axis_name") or "").strip()
                if axis and axis not in escalated_axes:
                    escalated_axes.append(axis)

        mean_p_top = _mean(p_tops)
        gap = None
        if confidence is not None:
            gap = round(tier_severity(tier) - confidence, 6)
        if gap is not None and gap > 0.0:
            divergent.append({
                "server_id": sid,
                "name": row.get("name"),
                "risk_tier": tier,
                "confidence": confidence,
                "mean_p_top": round(mean_p_top, 6) if mean_p_top is not None else None,
                "escalated_axes": escalated_axes,
                "confidence_gap": gap,
            })

    divergent.sort(key=lambda d: (-d["confidence_gap"], d["server_id"]))

    buckets: List[Dict[str, Any]] = []
    for tier in sorted(tiers):
        counts = band_counts.get(tier, {})
        if counts:
            band = sorted(counts.items(), key=lambda kv: (-kv[1], _band_sort_key(kv[0])))[0][0]
        else:
            band = UNKNOWN_BAND
        buckets.append({"risk_tier": tier, "confidence_band": band, "count": tiers[tier]})

    summary = (
        f"Audited {audited} servers across {len(tiers)} risk tiers; "
        f"{low_confidence_servers} below the {LOW_CONFIDENCE_THRESHOLD:g} confidence "
        f"threshold; {len(divergent)} tier/confidence divergences."
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "servers_audited": audited,
        "tiers": tiers,
        "low_confidence_servers": low_confidence_servers,
        "divergent": divergent,
        "buckets": buckets,
        "summary": summary,
    }


# --------------------------------------------------------------- data seam

def load_db_rows(db: Session) -> Dict[str, List[Dict[str, Any]]]:
    """Read the REAL app tables and return them as build_report() rows."""
    registry_rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.name,
               McpServerRegistry.risk_tier, McpServerRegistry.verdict,
               McpServerRegistry.confidence, McpServerRegistry.last_assessed)
    ).all()
    score_rows = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
               McpLlmAxisScore.p_top, McpLlmAxisScore.p_critical,
               McpLlmAxisScore.p_danger, McpLlmAxisScore.escalated,
               McpLlmAxisScore.scored_at, McpLlmAxisScore.decision_rule_version)
    ).all()

    registry = []
    for sid, name, tier, verdict, confidence, last_assessed in registry_rows:
        registry.append({
            "server_id": sid,
            "name": name,
            "risk_tier": tier,
            "verdict": verdict,
            "confidence": confidence,
            "last_assessed": (last_assessed.isoformat() if last_assessed else None),
        })

    scores = []
    for sid, axis, p_top, p_critical, p_danger, escalated, scored_at, drv in score_rows:
        scores.append({
            "server_id": sid,
            "axis_name": axis,
            "p_top": p_top,
            "p_critical": p_critical,
            "p_danger": p_danger,
            "escalated": bool(escalated),
            "scored_at": (scored_at.isoformat() if scored_at else None),
            "decision_rule_version": drv,
        })

    return {"mcp_server_registry": registry, "mcp_llm_axis_scores": scores}


def write_report(report: Dict[str, Any], path: Optional[str] = None) -> str:
    """Write the report as JSON and return the absolute-ish path written."""
    target = path or OUTPUT_PATH
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True, default=str)
    return target


def main(db_rows: Optional[Dict[str, List[Dict[str, Any]]]] = None,
         path: Optional[str] = None) -> int:
    """Generate the report and write it to disk; 0 on success.

    With no rows supplied the REAL app tables are read through the REAL
    app.db session dependency.
    """
    if db_rows is None:
        gen = get_session()
        session = next(gen)
        try:
            db_rows = load_db_rows(session)
        finally:
            gen.close()
    report = build_report(db_rows)
    written = write_report(report, path=path)
    print(f"[tier-confidence-divergence] {report['summary']} -> {written}")
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    synthetic = {
        "mcp_server_registry": [
            {"server_id": "srv-high", "name": "High Risk MCP",
             "risk_tier": "HIGH_RISK", "verdict": "flagged", "confidence": 0.2,
             "last_assessed": "2026-11-01T00:00:00+00:00"},
            {"server_id": "srv-low", "name": "Low Risk MCP",
             "risk_tier": "LOW", "verdict": "clean", "confidence": 0.9,
             "last_assessed": "2026-11-01T00:00:00+00:00"},
            {"server_id": "srv-noscores", "name": "Unscored MCP",
             "risk_tier": "MEDIUM", "verdict": "unreviewed", "confidence": None,
             "last_assessed": None},
        ],
        "mcp_llm_axis_scores": [
            {"server_id": "srv-high", "axis_name": "overall_risk", "p_top": 0.55,
             "p_critical": 0.2, "p_danger": 0.1, "escalated": False,
             "scored_at": "2026-11-01T00:00:00+00:00",
             "decision_rule_version": "v3.0"},
            {"server_id": "srv-high", "axis_name": "auth_strength", "p_top": 0.3,
             "p_critical": 0.1, "p_danger": 0.05, "escalated": True,
             "scored_at": "2026-11-01T00:00:00+00:00",
             "decision_rule_version": "v3.0"},
            {"server_id": "srv-low", "axis_name": "overall_risk", "p_top": 0.1,
             "p_critical": 0.0, "p_danger": 0.0, "escalated": False,
             "scored_at": "2026-11-01T00:00:00+00:00",
             "decision_rule_version": "v3.0"},
        ],
    }

    report = build_report(synthetic)
    assert report["servers_audited"] == 3, report
    assert report["tiers"] == {"HIGH_RISK": 1, "LOW": 1, "MEDIUM": 1}, report
    assert report["low_confidence_servers"] == 1, report
    assert len(report["buckets"]) == len(report["tiers"]), report
    assert sum(b["count"] for b in report["buckets"]) == 3, report
    div = {d["server_id"]: d for d in report["divergent"]}
    assert "srv-high" in div, report
    assert div["srv-high"]["confidence_gap"] > 0, report
    assert div["srv-high"]["escalated_axes"] == ["auth_strength"], report
    assert abs(div["srv-high"]["mean_p_top"] - 0.425) < 1e-9, report
    assert "srv-low" not in div, report
    assert report["generated_at"], report
    assert report["summary"], report

    # helper edge cases
    assert confidence_band(None) == UNKNOWN_BAND
    assert confidence_band(0.0) == "0.0-0.2"
    assert confidence_band(0.95) == "0.8-1.0"
    assert confidence_band(1.0) == "0.8-1.0"
    assert tier_severity("HIGH_RISK") == 2.0
    assert tier_severity("nonsense") == 0.0
    assert build_report({})["servers_audited"] == 0

    # real data layer wiring: read the real tables through the real model/session
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    try:
        s.add(McpServerRegistry(server_id="srv1", name="High Risk MCP",
                                url="https://github.com/example/high-risk",
                                registry_source="github", risk_tier="HIGH_RISK",
                                verdict="flagged", confidence=0.2))
        s.add(McpServerRegistry(server_id="srv2", name="Unscored MCP",
                                url="https://github.com/example/unscored",
                                registry_source="github", risk_tier="MEDIUM"))
        for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "WEAK")), start=1):
            s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                                  model_version="v3.0_40974559"))
        s.commit()
        db_rows = load_db_rows(s)
        assert len(db_rows["mcp_server_registry"]) == 2, db_rows
        assert len(db_rows["mcp_llm_axis_scores"]) == 2, db_rows
        rep2 = build_report(db_rows)
        assert rep2["servers_audited"] == 2, rep2
        assert rep2["tiers"] == {"HIGH_RISK": 1, "MEDIUM": 1}, rep2
        assert rep2["low_confidence_servers"] == 1, rep2
        assert len(rep2["divergent"]) == 1, rep2
    finally:
        s.close()

    # the same data through a LOCAL FastAPI app with the session dependency overridden
    app = FastAPI()

    @app.get("/report")
    def _report(db: Session = Depends(get_session)):
        return build_report(load_db_rows(db))

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
    assert resp.json()["servers_audited"] == 2, resp.text

    # write the JSON report and exit cleanly
    assert main(db_rows=synthetic) == 0
    assert os.path.exists(OUTPUT_PATH), OUTPUT_PATH
    with open(OUTPUT_PATH, encoding="utf-8") as fh:
        written = json.load(fh)
    assert written["servers_audited"] == 3, written

    print("PASS")
