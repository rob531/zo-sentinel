# deps: sqlalchemy
"""axis_label_taxonomy_drift_report.py -- audits axis LABEL TAXONOMY drift in the
axis-score corpus.

Drift = the label_index ordering (or the label set) of an axis is not stable
across scoring runs. When it is not, p_top comparisons across runs are not
comparable: "p_top=0.4" may mean different severities in two different runs.

Two layers:
  * pure layer -- build_report(rows) takes the axis-score rows from the caller
    and touches nothing else (stdlib only: no network, no file writes, no DB).
  * data seam -- load_drift_rows(db) reads the REAL app table through the REAL
    app session dependency (app.db.get_session) and app.models.McpLlmAxisScore,
    returning rows in the shape the pure layer expects.

Issue taxonomy (one finding per issue per axis):
  * duplicate_label_index -- inside ONE run, two distinct labels share a
    label_index (the ordinal is ambiguous).
  * label_index_conflict -- ONE label carries different label_index values in
    different rows (the ordinal moved under the label).
  * label_set_changed -- two runs of the same axis disagree on the label set
    (a label appeared or disappeared -> the taxonomy changed mid-corpus).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

_MAX_SAMPLE_LABELS = 5


# ---------------------------------------------------------------- pure layer

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_key(row: Dict[str, Any]) -> Tuple[str, str]:
    """A scoring run = decision_rule_version + model_version (either may be blank)."""
    rule = str(row.get("decision_rule_version") or "").strip()
    model = str(row.get("model_version") or "").strip()
    return (rule, model)


def _run_label(row: Dict[str, Any]) -> str:
    return str(row.get("label") or "").strip()


def _run_index(row: Dict[str, Any]) -> Optional[int]:
    idx = row.get("label_index")
    if idx is None:
        return None
    try:
        return int(idx)
    except (TypeError, ValueError):
        return None


def _sample(labels) -> List[str]:
    return sorted({str(l) for l in labels if l})[:_MAX_SAMPLE_LABELS]


def build_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Audit label-taxonomy drift over axis-score rows.

    rows: dicts with keys axis_name, label, label_index, scored_at,
    decision_rule_version, model_version (one row per axis score).

    Returns {"generated_at": iso8601, "axes_checked": int,
             "drift_found": bool,
             "findings": [{"axis_name", "issue", "detail", "sample_labels"}],
             "summary": {...}}. Pure: no DB, no network, no writes.
    """
    by_axis: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        axis = str(row.get("axis_name") or "").strip()
        if not axis:
            continue
        by_axis[axis].append(row)

    findings: List[Dict[str, Any]] = []

    for axis_name in sorted(by_axis):
        axis_rows = by_axis[axis_name]

        runs: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
        for row in axis_rows:
            runs[_run_key(row)].append(row)

        # 1) duplicate label_index inside a single run
        for run_key in sorted(runs):
            idx_to_labels: Dict[int, set] = defaultdict(set)
            for row in runs[run_key]:
                idx = _run_index(row)
                label = _run_label(row)
                if idx is not None and label:
                    idx_to_labels[idx].add(label)
            clashes = {i: l for i, l in idx_to_labels.items() if len(l) > 1}
            if clashes:
                idx = sorted(clashes)[0]
                findings.append({
                    "axis_name": axis_name,
                    "issue": "duplicate_label_index",
                    "detail": (
                        f"run rules={run_key[0] or '?'} model={run_key[1] or '?'}: "
                        f"label_index {idx} maps to multiple labels "
                        f"({len(clashes)} ambiguous index value(s))"
                    ),
                    "sample_labels": _sample(clashes[idx]),
                })
                break

        # 2) one label carrying different label_index values
        label_to_idx: Dict[str, set] = defaultdict(set)
        for row in axis_rows:
            idx = _run_index(row)
            label = _run_label(row)
            if idx is not None and label:
                label_to_idx[label].add(idx)
        moved = {l: s for l, s in label_to_idx.items() if len(s) > 1}
        if moved:
            label = sorted(moved)[0]
            findings.append({
                "axis_name": axis_name,
                "issue": "label_index_conflict",
                "detail": (
                    f"label '{label}' carries {len(moved[label])} different "
                    f"label_index value(s) {sorted(moved[label])}"
                ),
                "sample_labels": _sample([label]),
            })

        # 3) label set changed between runs
        run_label_sets = {
            key: {_run_label(r) for r in rrows if _run_label(r)}
            for key, rrows in runs.items()
        }
        distinct_sets = {frozenset(s) for s in run_label_sets.values()}
        if len(distinct_sets) > 1:
            ordered = sorted(run_label_sets.items())
            first_key, first_set = ordered[0]
            other_key, other_set = ordered[-1]
            added = sorted(other_set - first_set)
            removed = sorted(first_set - other_set)
            findings.append({
                "axis_name": axis_name,
                "issue": "label_set_changed",
                "detail": (
                    f"{len(distinct_sets)} distinct label sets across "
                    f"{len(run_label_sets)} run(s); "
                    f"rules={first_key[0] or '?'} model={first_key[1] or '?'} vs "
                    f"rules={other_key[0] or '?'} model={other_key[1] or '?'}"
                    + (f"; added={added}" if added else "")
                    + (f"; removed={removed}" if removed else "")
                ),
                "sample_labels": _sample(added + removed),
            })

    axes_with_drift = {f["axis_name"] for f in findings}
    issue_counts: Dict[str, int] = defaultdict(int)
    for f in findings:
        issue_counts[f["issue"]] += 1

    summary = {
        "axes_checked": len(by_axis),
        "axes_with_drift": len(axes_with_drift),
        "total_findings": len(findings),
        "issues": dict(sorted(issue_counts.items())),
        "rows_audited": sum(len(v) for v in by_axis.values()),
    }

    return {
        "generated_at": _iso_now(),
        "axes_checked": len(by_axis),
        "drift_found": bool(findings),
        "findings": findings,
        "summary": summary,
    }


def format_text_report(report: Dict[str, Any]) -> str:
    """Render the drift report as plain text."""
    rep = report or {}
    lines = ["Axis label taxonomy drift", "=" * 38]
    summary = rep.get("summary") or {}
    lines.append(f"axes checked : {summary.get('axes_checked', 0)}")
    lines.append(f"rows audited : {summary.get('rows_audited', 0)}")
    lines.append(f"drift found  : {rep.get('drift_found', False)}")
    lines.append("")
    for f in rep.get("findings") or []:
        lines.append(f"[{f.get('issue')}] {f.get('axis_name')}")
        lines.append(f"  {f.get('detail')}")
        if f.get("sample_labels"):
            lines.append(f"  labels: {', '.join(f['sample_labels'])}")
        lines.append("")
    if not (rep.get("findings") or []):
        lines.append("(no drift)")
    return "\n".join(lines)


# --------------------------------------------------------------- data seam

def load_drift_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the REAL axis scores and return them as report rows."""
    stmt = select(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label,
        McpLlmAxisScore.label_index,
        McpLlmAxisScore.scored_at,
        McpLlmAxisScore.decision_rule_version,
        McpLlmAxisScore.model_version,
    ).where(McpLlmAxisScore.label.isnot(None))
    return [
        {
            "axis_name": axis,
            "label": label,
            "label_index": idx,
            "scored_at": scored_at.isoformat() if hasattr(scored_at, "isoformat") else scored_at,
            "decision_rule_version": rule,
            "model_version": model,
        }
        for axis, label, idx, scored_at, rule, model in db.execute(stmt).all()
    ]


# ------------------------------------------------------------------- CLI

def _self_test() -> int:
    # --- clean axis only -> no drift ---
    clean_rows = [
        {"axis_name": "overall_risk", "label": "LOW", "label_index": 0,
         "scored_at": "2026-10-01T00:00:00+00:00",
         "decision_rule_version": "rules-v1", "model_version": "v3.0_40974559"},
        {"axis_name": "overall_risk", "label": "HIGH", "label_index": 1,
         "scored_at": "2026-10-01T00:00:00+00:00",
         "decision_rule_version": "rules-v1", "model_version": "v3.0_40974559"},
    ]
    clean = build_report(clean_rows)
    assert clean["drift_found"] is False, clean
    assert clean["findings"] == [], clean
    assert clean["axes_checked"] == 1, clean

    # --- axis with duplicate label_index values ---
    dup_rows = [
        {"axis_name": "auth_strength", "label": "STRONG", "label_index": 0,
         "scored_at": "2026-10-01T00:00:00+00:00",
         "decision_rule_version": "rules-v1", "model_version": "v3.0_40974559"},
        {"axis_name": "auth_strength", "label": "WEAK", "label_index": 0,
         "scored_at": "2026-10-01T00:00:00+00:00",
         "decision_rule_version": "rules-v1", "model_version": "v3.0_40974559"},
    ]
    dup = build_report(dup_rows)
    assert dup["drift_found"] is True, dup
    assert len(dup["findings"]) == 1, dup
    assert dup["findings"][0]["issue"] == "duplicate_label_index", dup
    assert dup["findings"][0]["axis_name"] == "auth_strength", dup

    # --- axis whose label set changed between runs ---
    changed_rows = [
        {"axis_name": "network_egress", "label": "INTERNAL", "label_index": 0,
         "scored_at": "2026-10-01T00:00:00+00:00",
         "decision_rule_version": "rules-v1", "model_version": "v3.0_40974559"},
        {"axis_name": "network_egress", "label": "EXTERNAL", "label_index": 1,
         "scored_at": "2026-10-01T00:00:00+00:00",
         "decision_rule_version": "rules-v1", "model_version": "v3.0_40974559"},
        {"axis_name": "network_egress", "label": "RESTRICTED", "label_index": 0,
         "scored_at": "2026-10-02T00:00:00+00:00",
         "decision_rule_version": "rules-v2", "model_version": "v3.0_40974559"},
        {"axis_name": "network_egress", "label": "EXTERNAL", "label_index": 1,
         "scored_at": "2026-10-02T00:00:00+00:00",
         "decision_rule_version": "rules-v2", "model_version": "v3.0_40974559"},
    ]
    changed = build_report(changed_rows)
    assert changed["drift_found"] is True, changed
    assert len(changed["findings"]) == 1, changed
    assert changed["findings"][0]["issue"] == "label_set_changed", changed

    # --- combined: exactly 2 findings ---
    combined = build_report(clean_rows + dup_rows + changed_rows)
    assert combined["drift_found"] is True, combined
    assert len(combined["findings"]) == 2, combined
    assert combined["axes_checked"] == 3, combined
    assert combined["summary"]["axes_with_drift"] == 2, combined
    assert combined["summary"]["total_findings"] == 2, combined
    assert {f["axis_name"] for f in combined["findings"]} == \
        {"auth_strength", "network_egress"}, combined
    json.dumps(combined)  # must be JSON-serialisable

    # --- empty input ---
    empty = build_report([])
    assert empty["drift_found"] is False, empty
    assert empty["axes_checked"] == 0, empty

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
        # distinct server_id per row: UNIQUE(server_id, axis_name, model_version)
        s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                              label="HIGH", label_index=1,
                              decision_rule_version="rules-v1",
                              model_version="v3.0_40974559"))
        s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="overall_risk",
                              label="LOW", label_index=0,
                              decision_rule_version="rules-v1",
                              model_version="v3.0_40974559"))
        s.add(McpLlmAxisScore(id=3, server_id="srv3", axis_name="auth_strength",
                              label="STRONG", label_index=0,
                              decision_rule_version="rules-v1",
                              model_version="v3.0_40974559"))
        s.add(McpLlmAxisScore(id=4, server_id="srv4", axis_name="auth_strength",
                              label="WEAK", label_index=0,
                              decision_rule_version="rules-v1",
                              model_version="v3.0_40974559"))
        s.commit()
        loaded = load_drift_rows(s)
        assert len(loaded) == 4, loaded
        db_rep = build_report(loaded)
        assert db_rep["axes_checked"] == 2, db_rep
        assert db_rep["drift_found"] is True, db_rep
        assert db_rep["findings"][0]["issue"] == "duplicate_label_index", db_rep
        assert "duplicate_label_index" in format_text_report(db_rep)
    finally:
        s.close()

    print("PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Axis label taxonomy drift report")
    ap.add_argument("--db", action="store_true",
                    help="read the real mcp_llm_axis_scores rows via app.db.get_session")
    ap.add_argument("--format", choices=("text", "json"), default="text")
    ap.add_argument("--self-test", action="store_true", help="run the built-in self-test")
    args = ap.parse_args(argv)

    if args.db:
        gen = get_session()
        db = next(gen)
        try:
            rows = load_drift_rows(db)
        finally:
            gen.close()
        report = build_report(rows)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_text_report(report))
        return 0

    return _self_test()


if __name__ == "__main__":
    raise SystemExit(main())
