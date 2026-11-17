# deps: sqlalchemy
"""axis_label_mix_report.py -- how MIXED the LLM scoring model is per risk axis.

Pure layer  : build_axis_label_mix_report(rows) -> {"generated_at": iso,
                 "axes": [{"axis_name", "label", "count", "share"}]}
Data seam   : load_mix_rows(db, model_version) reads the REAL app table
               (mcp_llm_axis_scores) through app.db.get_session and
               app.models.McpLlmAxisScore.

Read-only: no DB writes, no network calls.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


# ---------------------------------------------------------------- pure layer

class AxisLabelMixReport(dict):
    """dict subclass whose __str__ renders the plain-text summary."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return format_mix_text(dict(self))


def build_axis_label_mix_report(rows: List[Dict[str, Any]]) -> AxisLabelMixReport:
    """Per-axis label mix.

    rows: dicts with keys axis_name (str), label (str), optionally p_top (float).
    Rows without a usable axis_name or label are ignored.

    Returns {"generated_at": <ISO 8601 UTC>,
             "axes": [{"axis_name": str, "label": str,
                       "count": int, "share": float}, ...]}
    `share` is the label's fraction of its own axis; the shares of one axis
    therefore sum to ~1.0. Entries are ordered by axis name, then by descending
    count, then by descending mean p_top, then label -- deterministic.
    """
    counts: Dict[str, Counter] = defaultdict(Counter)
    psum: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        label = str(row.get("label") or "").strip()
        if not axis or not label:
            continue
        counts[axis][label] += 1
        ptop = row.get("p_top")
        if isinstance(ptop, (int, float)) and not isinstance(ptop, bool):
            psum[axis][label] += float(ptop)

    entries: List[Dict[str, Any]] = []
    for axis_name in sorted(counts):
        axis_counts = counts[axis_name]
        total = sum(axis_counts.values())
        if not total:
            continue

        def _key(label: str):
            mean_p = psum[axis_name].get(label, 0.0) / axis_counts[label]
            return (-axis_counts[label], -mean_p, label)

        for label in sorted(axis_counts, key=_key):
            entries.append({
                "axis_name": axis_name,
                "label": label,
                "count": int(axis_counts[label]),
                "share": (axis_counts[label] / total) if total else 0.0,
            })

    return AxisLabelMixReport({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": entries,
    })


def format_mix_text(report: Dict[str, Any]) -> str:
    """Render the mix report as plain text."""
    lines: List[str] = []
    lines.append("Axis label mix")
    lines.append("=" * 38)
    entries = (report or {}).get("axes") or []
    if not entries:
        lines.append("(no scored rows)")
        return "\n".join(lines)
    current = None
    for e in entries:
        if e.get("axis_name") != current:
            current = e.get("axis_name")
            lines.append(str(current))
        share = e.get("share", 0.0)
        lines.append(f"  {str(e.get('label', '')):<12} {int(e.get('count', 0)):>7}  {share * 100:6.2f}%")
    lines.append("")
    lines.append(f"generated_at: {(report or {}).get('generated_at', '')}")
    return "\n".join(lines)


# --------------------------------------------------------------- data seam

def _latest_model_version(db: Session) -> Optional[str]:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .where(McpLlmAxisScore.label.isnot(None))
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def load_mix_rows(db: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as report rows."""
    mv = model_version or _latest_model_version(db)
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.p_top,
    ).where(McpLlmAxisScore.label.isnot(None))
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    return [
        {"server_id": sid, "axis_name": axis, "label": label, "p_top": ptop}
        for sid, axis, label, ptop in db.execute(stmt).all()
    ]


# ------------------------------------------------------------------- CLI

def _self_test() -> int:
    # --- pure layer: 3 synthetic rows across 2 axis_names, one label per axis ---
    rows = [
        {"axis_name": "overall_risk", "label": "HIGH", "p_top": 0.72},
        {"axis_name": "overall_risk", "label": "HIGH", "p_top": 0.61},
        {"axis_name": "auth_strength", "label": "STRONG", "p_top": 0.55},
    ]
    rep = build_axis_label_mix_report(rows)
    assert set(rep.keys()) == {"generated_at", "axes"}, rep
    assert len(rep["axes"]) == 2, rep
    assert isinstance(rep["generated_at"], str) and rep["generated_at"], rep

    per_axis: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in rep["axes"]:
        per_axis[e["axis_name"]].append(e)
    assert sorted(per_axis) == ["auth_strength", "overall_risk"], rep
    for axis, es in per_axis.items():
        assert sum(int(e["count"]) for e in es) == sum(
            1 for r in rows if r["axis_name"] == axis), (axis, es)
        for e in es:
            assert isinstance(e["share"], float), e
            assert 0.0 <= e["share"] <= 1.0, e
        assert abs(sum(e["share"] for e in es) - 1.0) < 1e-9, (axis, es)

    # --- pure layer: mixed labels inside one axis ---
    mixed = [
        {"axis_name": "overall_risk", "label": "HIGH", "p_top": 0.9},
        {"axis_name": "overall_risk", "label": "LOW", "p_top": 0.4},
        {"axis_name": "overall_risk", "label": "HIGH", "p_top": 0.8},
        {"axis_name": "data_sensitivity", "label": "HIGH", "p_top": 0.7},
    ]
    rep2 = build_axis_label_mix_report(mixed)
    ov = [e for e in rep2["axes"] if e["axis_name"] == "overall_risk"]
    assert len(ov) == 2, rep2
    assert [(e["label"], e["count"]) for e in ov] == [("HIGH", 2), ("LOW", 1)], rep2
    assert abs(sum(e["share"] for e in ov) - 1.0) < 1e-9, rep2
    ds = [e for e in rep2["axes"] if e["axis_name"] == "data_sensitivity"]
    assert abs(sum(e["share"] for e in ds) - 1.0) < 1e-9, rep2

    # --- pure layer: empty / malformed input ---
    empty = build_axis_label_mix_report([])
    assert empty["axes"] == [], empty
    assert build_axis_label_mix_report([{"axis_name": "", "label": "X"}])["axes"] == []

    # --- DB path: real app models over SQLite ---
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
        for i, (sid, ax, lbl) in enumerate((
                ("srv1", "overall_risk", "HIGH"),
                ("srv1", "auth_strength", "STRONG"),
                ("srv2", "overall_risk", "LOW")), start=1):
            s.add(McpLlmAxisScore(id=i, server_id=sid, axis_name=ax, label=lbl,
                                  model_version="v3.0_40974559"))
        s.commit()
        loaded = load_mix_rows(s)
        assert len(loaded) == 3, loaded
        rep3 = build_axis_label_mix_report(loaded)
        assert len(rep3["axes"]) == 3, rep3
        assert "overall_risk" in str(rep3)
        assert "Axis label mix" in format_mix_text(rep3)
    finally:
        s.close()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Axis label mix report")
    ap.add_argument("--db", action="store_true",
                    help="read the real mcp_llm_axis_scores rows via app.db.get_session")
    ap.add_argument("--model-version", default=None, help="restrict to one model_version")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--self-test", action="store_true", help="run the built-in self-test")
    args = ap.parse_args(argv)

    if args.db:
        gen = get_session()
        db = next(gen)
        try:
            rows = load_mix_rows(db, model_version=args.model_version)
        finally:
            gen.close()
        report = build_axis_label_mix_report(rows)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_mix_text(report))
        return 0

    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
