"""axis_label_drift_report.py -- axis-label drift report generator.

Audits whether the axis labels stored in mcp_llm_axis_scores drift over time:
if an axis label changes (or its label_index moves) the meaning of the risk
model silently changes, so the drift must be surfaced.

The core is PURE (`build_drift_report(rows) -> dict`): the caller supplies the
rows, so the analysis is testable without a database. `run()` / `build_report()`
supply those rows from the REAL app data layer (app.db / app.models) via
`get_session`.

Read-only: no writes to any table, no network calls.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

VERDICT_DRIFT = "DRIFT_DETECTED"
VERDICT_STABLE = "NO_DRIFT"


@contextmanager
def _session_scope() -> Iterator[Session]:
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


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_drift_report(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure drift analysis over caller-supplied rows.

    Each row is a dict with keys axis_name (str), label (str), label_index (int)
    and scored_at (iso8601 str).
    """
    if rows is None:
        rows = []
    if not isinstance(rows, (list, tuple)):
        raise TypeError("rows must be a list of dicts")

    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        axis_name = row.get("axis_name")
        if not axis_name:
            continue
        axis = str(axis_name)
        slot = buckets.setdefault(axis, {"labels": set(), "indexes": set(), "stamps": []})
        label = row.get("label")
        if label is not None:
            slot["labels"].add(str(label))
        index = row.get("label_index")
        if index is not None:
            try:
                slot["indexes"].add(int(index))
            except (TypeError, ValueError):
                pass
        stamp = _iso(row.get("scored_at"))
        if stamp:
            slot["stamps"].append(stamp)

    drifted_axes: List[Dict[str, Any]] = []
    stable_axes: List[str] = []

    for axis_name in sorted(buckets):
        slot = buckets[axis_name]
        labels = sorted(slot["labels"])
        indexes = sorted(slot["indexes"])
        stamps = sorted(slot["stamps"])
        entry = {
            "axis_name": axis_name,
            "distinct_labels": labels,
            "distinct_label_indexes": indexes,
            "first_seen": stamps[0] if stamps else None,
            "last_seen": stamps[-1] if stamps else None,
        }
        if len(labels) > 1 or len(indexes) > 1:
            drifted_axes.append(entry)
        else:
            stable_axes.append(axis_name)

    return {
        "axes_examined": len(buckets),
        "drifted_axes": drifted_axes,
        "stable_axes": stable_axes,
        "verdict": VERDICT_DRIFT if drifted_axes else VERDICT_STABLE,
    }


def fetch_rows(session: Session) -> List[Dict[str, Any]]:
    """Read (axis_name, label, label_index, scored_at) from mcp_llm_axis_scores."""
    rows = session.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.scored_at,
        )
    ).all()
    return [
        {
            "axis_name": axis_name,
            "label": label,
            "label_index": label_index,
            "scored_at": _iso(scored_at),
        }
        for axis_name, label, label_index, scored_at in rows
    ]


def build_report(session: Optional[Session] = None) -> Dict[str, Any]:
    """Drift report for the stored axis labels."""
    if session is not None:
        return build_drift_report(fetch_rows(session))
    with _session_scope() as s:
        return build_drift_report(fetch_rows(s))


def render_text(report: Dict[str, Any]) -> str:
    """Plain-text rendering of a build_drift_report() result."""
    lines: List[str] = []
    lines.append("MCP LLM AXIS SCORES -- AXIS LABEL DRIFT REPORT")
    lines.append("=" * 60)
    lines.append(f"axes_examined : {report.get('axes_examined', 0)}")
    lines.append(f"verdict       : {report.get('verdict', '')}")
    lines.append("")
    lines.append("drifted_axes")
    lines.append("-" * 60)
    drifted = list(report.get("drifted_axes") or [])
    if drifted:
        for item in drifted:
            lines.append(
                "{axis_name} | labels={labels} | indexes={indexes} | "
                "{first_seen} -> {last_seen}".format(
                    axis_name=item.get("axis_name", ""),
                    labels=item.get("distinct_labels", []),
                    indexes=item.get("distinct_label_indexes", []),
                    first_seen=item.get("first_seen", ""),
                    last_seen=item.get("last_seen", ""),
                )
            )
    else:
        lines.append("(none)")
    lines.append("")
    lines.append("stable_axes")
    lines.append("-" * 60)
    lines.append(", ".join(report.get("stable_axes") or []) or "(none)")
    return "\n".join(lines)


def run() -> int:
    """Generate the drift report from the live data layer. 0 = no drift, 1 = drift."""
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True))
    print(render_text(report))
    return 1 if report.get("verdict") == VERDICT_DRIFT else 0


try:  # FastAPI surface is optional -- the module works standalone.
    from fastapi import APIRouter, Depends

    router = APIRouter(prefix="/api", tags=["axis-label-drift"])

    @router.get("/axis-label-drift")
    def axis_label_drift(db: Session = Depends(get_session)) -> dict:
        """Drift report for the stored axis labels."""
        return build_report(session=db)
except Exception:  # pragma: no cover - fastapi always present in deploy
    router = None


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                          label="STRONG", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv2", axis_name="overall_risk",
                          label="MEDIUM", model_version="v3.0_40974559"))
    s.commit()

    # (a) one axis with a single label and label_index -> NO_DRIFT
    rows_a = [{"axis_name": "overall_risk", "label": "HIGH", "label_index": 2,
               "scored_at": "2026-01-01T00:00:00+00:00"}]
    rep_a = build_drift_report(rows_a)
    assert rep_a["verdict"] == VERDICT_STABLE, rep_a
    assert rep_a["drifted_axes"] == [], rep_a
    assert rep_a["axes_examined"] == 1, rep_a
    assert rep_a["stable_axes"] == ["overall_risk"], rep_a

    # (b) one axis with two different labels but an identical label_index -> DRIFT
    rows_b = [{"axis_name": "overall_risk", "label": "HIGH", "label_index": 2,
               "scored_at": "2026-01-01T00:00:00+00:00"},
              {"axis_name": "overall_risk", "label": "MEDIUM", "label_index": 2,
               "scored_at": "2026-02-01T00:00:00+00:00"}]
    rep_b = build_drift_report(rows_b)
    assert rep_b["verdict"] == VERDICT_DRIFT, rep_b
    assert len(rep_b["drifted_axes"]) == 1, rep_b
    assert rep_b["drifted_axes"][0]["axis_name"] == "overall_risk", rep_b
    assert rep_b["drifted_axes"][0]["distinct_labels"] == ["HIGH", "MEDIUM"], rep_b
    assert rep_b["drifted_axes"][0]["distinct_label_indexes"] == [2], rep_b
    assert rep_b["drifted_axes"][0]["first_seen"] == "2026-01-01T00:00:00+00:00", rep_b
    assert rep_b["drifted_axes"][0]["last_seen"] == "2026-02-01T00:00:00+00:00", rep_b

    # (c) two axes each stable -> drifted_axes empty
    rows_c = [{"axis_name": "overall_risk", "label": "HIGH", "label_index": 2,
               "scored_at": "2026-01-01T00:00:00+00:00"},
              {"axis_name": "auth_strength", "label": "STRONG", "label_index": 0,
               "scored_at": "2026-01-01T00:00:00+00:00"}]
    rep_c = build_drift_report(rows_c)
    assert rep_c["verdict"] == VERDICT_STABLE, rep_c
    assert rep_c["drifted_axes"] == [], rep_c
    assert rep_c["axes_examined"] == 2, rep_c
    assert sorted(rep_c["stable_axes"]) == ["auth_strength", "overall_risk"], rep_c

    # edge case: empty input is stable, not an error
    rep_d = build_drift_report([])
    assert rep_d["verdict"] == VERDICT_STABLE, rep_d
    assert rep_d["axes_examined"] == 0, rep_d

    # the same analysis over rows read from the real data layer
    report_db = build_report(session=s)
    assert report_db["axes_examined"] == 2, report_db
    assert report_db["verdict"] == VERDICT_DRIFT, report_db
    assert report_db["drifted_axes"][0]["axis_name"] == "overall_risk", report_db
    assert "verdict" in report_db and "stable_axes" in report_db, report_db
    assert render_text(report_db), "render_text"

    app = FastAPI()
    if router is not None:
        app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/axis-label-drift")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["axes_examined"] == 2, j
    assert j["verdict"] == VERDICT_DRIFT, j
    s.close()

    print("PASS")
