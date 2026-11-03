# deps: sqlalchemy
"""axis_label_distribution_report.py -- how the LLM model spreads the 7 risk-axis
labels across the scored corpus.

Two layers:
  * pure layer -- build_axis_label_distribution(rows) takes the axis-score rows
    from the caller and touches nothing else (no network, no file writes, no DB).
  * data seam -- load_label_rows(db, model_version) reads the REAL app table
    through the REAL app session dependency (app.db.get_session) and
    app.models.McpLlmAxisScore, returning rows in the shape the pure layer
    expects.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")


# ---------------------------------------------------------------- pure layer

def build_axis_label_distribution(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Label distribution per risk axis.

    rows: dicts with keys axis_name (str), label (str), server_id (str) -- one
    row per axis score. Rows without a usable axis_name or label are ignored.

    Returns {"axes": [{"axis_name": str,
                       "label_counts": [{"label": str, "count": int,
                                         "share": float}],
                       "server_count": int}],
             "total_rows": int}. Axes are sorted by name; labels are ordered by
    descending count then label; share is the label's fraction of its axis.
    """
    by_axis: Dict[str, List[Dict[str, Any]]] = {}
    total_rows = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        label = str(row.get("label") or "").strip()
        sid = str(row.get("server_id") or "").strip()
        if not axis or not label:
            continue
        total_rows += 1
        by_axis.setdefault(axis, []).append({"label": label, "server_id": sid})

    axes: List[Dict[str, Any]] = []
    for axis_name in sorted(by_axis):
        entries = by_axis[axis_name]
        counts = Counter(e["label"] for e in entries)
        total = sum(counts.values())
        servers = {e["server_id"] for e in entries if e["server_id"]}
        label_counts = [
            {
                "label": label,
                "count": counts[label],
                "share": counts[label] / total if total else 0.0,
            }
            for label in sorted(counts, key=lambda lb: (-counts[lb], lb))
        ]
        axes.append({
            "axis_name": axis_name,
            "label_counts": label_counts,
            "server_count": len(servers),
        })

    return {"axes": axes, "total_rows": total_rows}


def format_text_report(report: Dict[str, Any]) -> str:
    """Render the distribution as a plain-text report."""
    lines: List[str] = []
    lines.append("Axis label distribution")
    lines.append("=" * 38)
    for entry in (report or {}).get("axes") or []:
        lines.append(f"{entry.get('axis_name', '')}  "
                     f"(servers {entry.get('server_count', 0)})")
        for lc in entry.get("label_counts") or []:
            share = lc.get("share", 0.0)
            lines.append(f"  {lc.get('label', ''):<12} "
                         f"{lc.get('count', 0):>7}  {share * 100:6.2f}%")
        lines.append("")
    if not (report or {}).get("axes"):
        lines.append("(no scored rows)")
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


def load_label_rows(db: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as report rows."""
    mv = model_version or _latest_model_version(db)
    stmt = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
    ).where(McpLlmAxisScore.label.isnot(None))
    if mv:
        stmt = stmt.where(McpLlmAxisScore.model_version == mv)
    return [
        {"server_id": sid, "axis_name": axis, "label": label}
        for sid, axis, label in db.execute(stmt).all()
    ]


# ------------------------------------------------------------------- CLI

def _self_test() -> int:
    # --- pure layer: one row per axis for a single server ---
    single = [
        {"axis_name": ax, "label": lb, "server_id": "s1"}
        for ax, lb in (
            ("overall_risk", "HIGH"),
            ("auth_strength", "STRONG"),
            ("capability_breadth", "BROAD"),
            ("data_sensitivity", "CRITICAL"),
            ("network_egress", "EXTERNAL"),
            ("maintainer_trust", "ESTABLISHED"),
            ("exploit_surface", "MODERATE"),
        )
    ]
    rep = build_axis_label_distribution(single)
    assert rep["total_rows"] == 7, rep
    assert len(rep["axes"]) == 7, rep
    for entry in rep["axes"]:
        assert entry["server_count"] == 1, entry
        assert len(entry["label_counts"]) == 1, entry
        assert abs(sum(lc["share"] for lc in entry["label_counts"]) - 1.0) < 1e-9, entry

    # --- pure layer: skewed two-server set ---
    skewed = [
        {"axis_name": "overall_risk", "label": "HIGH", "server_id": "s1"},
        {"axis_name": "overall_risk", "label": "HIGH", "server_id": "s2"},
        {"axis_name": "overall_risk", "label": "LOW", "server_id": "s3"},
        {"axis_name": "auth_strength", "label": "STRONG", "server_id": "s1"},
        {"axis_name": "auth_strength", "label": "WEAK", "server_id": "s2"},
    ]
    rep2 = build_axis_label_distribution(skewed)
    assert rep2["total_rows"] == 5, rep2
    assert [e["axis_name"] for e in rep2["axes"]] == ["auth_strength", "overall_risk"], rep2
    overall = next(e for e in rep2["axes"] if e["axis_name"] == "overall_risk")
    assert overall["server_count"] == 3, overall
    assert [(lc["label"], lc["count"]) for lc in overall["label_counts"]] == \
        [("HIGH", 2), ("LOW", 1)], overall
    assert abs(sum(lc["share"] for lc in overall["label_counts"]) - 1.0) < 1e-9, overall
    auth = next(e for e in rep2["axes"] if e["axis_name"] == "auth_strength")
    assert auth["server_count"] == 2, auth
    assert abs(sum(lc["share"] for lc in auth["label_counts"]) - 1.0) < 1e-9, auth

    # --- pure layer: empty input ---
    empty = build_axis_label_distribution([])
    assert empty == {"axes": [], "total_rows": 0}, empty

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
        loaded = load_label_rows(s)
        assert len(loaded) == 3, loaded
        rep3 = build_axis_label_distribution(loaded)
        assert rep3["total_rows"] == 3, rep3
        ov = next(e for e in rep3["axes"] if e["axis_name"] == "overall_risk")
        assert ov["server_count"] == 2, ov
        assert [(lc["label"], lc["count"]) for lc in ov["label_counts"]] == \
            [("HIGH", 1), ("LOW", 1)], ov
        assert "overall_risk" in format_text_report(rep3)
    finally:
        s.close()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Axis label distribution report")
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
            rows = load_label_rows(db, model_version=args.model_version)
        finally:
            gen.close()
        report = build_axis_label_distribution(rows)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_text_report(report))
        return 0

    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
