"""axis_label_index_anomaly_report.py -- label_index integrity audit for LLM axis scores.

Audits each scored server's 7 axis rows in mcp_llm_axis_scores and flags servers
whose label_index values are DUPLICATED, NON-CONTIGUOUS, or whose axes are
MISSING (or carry a NULL label_index).

The core is PURE (`analyse_axis_labels(rows) -> dict`): the caller supplies the
rows, so the analysis is testable without a database. `build_report(rows)` /
`run()` supply those rows from the REAL app data layer (app.db / app.models)
via `get_session`.

Read-only: no writes to any table, no network calls. The only artefact written
is the JSON report under shared/outputs/goose/.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

# The 7 risk axes a fully-scored server must have.
VALID_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

ISSUE_MISSING_AXES = "missing_axes"
ISSUE_DUPLICATE_INDEX = "duplicate_label_index"
ISSUE_NON_CONTIGUOUS = "non_contiguous_label_index"
ISSUE_NULL_INDEX = "null_label_index"
ISSUE_UNKNOWN_AXIS = "unknown_axis"

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(REPO_ROOT, "shared", "outputs", "goose")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "axis_label_index_anomaly_report.json")


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


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def analyse_axis_labels(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure label_index integrity analysis over caller-supplied rows.

    Each row is a dict with keys server_id (str), axis_name (str),
    label_index (int), label (str).
    """
    if rows is None:
        rows = []
    if not isinstance(rows, (list, tuple)):
        raise TypeError("rows must be a list of dicts")

    by_server: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        server_id = row.get("server_id")
        if not server_id:
            continue
        by_server.setdefault(str(server_id), []).append(row)

    anomalies: List[Dict[str, Any]] = []

    for server_id in sorted(by_server):
        server_rows = by_server[server_id]
        axis_names: List[str] = []
        index_to_axes: Dict[int, List[str]] = {}
        null_axes: List[str] = []

        for row in server_rows:
            axis = str(row.get("axis_name") or "")
            if axis:
                axis_names.append(axis)
            index = row.get("label_index")
            if index is None:
                if axis:
                    null_axes.append(axis)
                continue
            try:
                index_int = int(index)
            except (TypeError, ValueError):
                if axis:
                    null_axes.append(axis)
                continue
            index_to_axes.setdefault(index_int, []).append(axis)

        # 1. missing axes (the server does not have all 7)
        missing = [a for a in VALID_AXES if a not in set(axis_names)]
        if missing:
            anomalies.append({
                "server_id": server_id,
                "issue": ISSUE_MISSING_AXES,
                "axes": sorted(missing),
            })

        # 2. duplicated label_index (two axes sharing one index)
        for index_int in sorted(index_to_axes):
            axes = index_to_axes[index_int]
            if len(axes) > 1:
                anomalies.append({
                    "server_id": server_id,
                    "issue": ISSUE_DUPLICATE_INDEX,
                    "axes": sorted(axes),
                })

        # 3. non-contiguous label_index (gaps in 0..n-1)
        unique = sorted(index_to_axes)
        if unique and unique != list(range(len(unique))):
            anomalies.append({
                "server_id": server_id,
                "issue": ISSUE_NON_CONTIGUOUS,
                "axes": sorted({a for i in unique for a in index_to_axes[i]}),
            })

        # 4. NULL / non-numeric label_index
        if null_axes:
            anomalies.append({
                "server_id": server_id,
                "issue": ISSUE_NULL_INDEX,
                "axes": sorted(set(null_axes)),
            })

        # 5. axis names outside the canonical 7
        unknown = sorted({a for a in axis_names if a not in VALID_AXES})
        if unknown:
            anomalies.append({
                "server_id": server_id,
                "issue": ISSUE_UNKNOWN_AXIS,
                "axes": unknown,
            })

    return {
        "checked": len(rows),
        "servers": len(by_server),
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "servers_affected": len({a["server_id"] for a in anomalies}),
    }


def fetch_rows(session: Session) -> List[Dict[str, Any]]:
    """Read the current-model axis rows from mcp_llm_axis_scores."""
    rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.scored_at,
        ).order_by(McpLlmAxisScore.server_id, McpLlmAxisScore.scored_at.desc())
    ).all()

    # Keep only the newest model_version per server (a verdict = its 7 rows
    # for the current model_version).
    newest: Dict[str, Optional[str]] = {}
    for server_id, _axis, _label, _idx, model_version, _scored in rows:
        key = str(server_id)
        if key not in newest:
            newest[key] = model_version

    out: List[Dict[str, Any]] = []
    for server_id, axis_name, label, label_index, model_version, _scored in rows:
        key = str(server_id)
        if newest.get(key) != model_version:
            continue
        out.append({
            "server_id": key,
            "axis_name": str(axis_name or ""),
            "label": None if label is None else str(label),
            "label_index": label_index,
        })
    return out


def build_report(rows: Optional[List[Dict[str, Any]]] = None,
                 session: Optional[Session] = None) -> Dict[str, Any]:
    """Label_index integrity report. Uses the live data layer when rows are omitted."""
    if rows is None:
        if session is not None:
            rows = fetch_rows(session)
        else:
            with _session_scope() as s:
                rows = fetch_rows(s)
    report = analyse_axis_labels(rows)
    report["generated_at"] = _iso_now()
    return report


def render_text(report: Dict[str, Any]) -> str:
    """Plain-text rendering of an analyse_axis_labels() result."""
    lines: List[str] = []
    lines.append("MCP LLM AXIS SCORES -- LABEL INDEX ANOMALY REPORT")
    lines.append("=" * 60)
    lines.append(f"checked          : {report.get('checked', 0)}")
    lines.append(f"servers          : {report.get('servers', 0)}")
    lines.append(f"anomaly_count    : {report.get('anomaly_count', 0)}")
    lines.append(f"servers_affected : {report.get('servers_affected', 0)}")
    lines.append("")
    lines.append("anomalies")
    lines.append("-" * 60)
    anomalies = list(report.get("anomalies") or [])
    if anomalies:
        for item in anomalies:
            lines.append("{server_id} | {issue} | axes={axes}".format(
                server_id=item.get("server_id", ""),
                issue=item.get("issue", ""),
                axes=", ".join(item.get("axes") or []),
            ))
    else:
        lines.append("(none)")
    return "\n".join(lines)


def write_artifact(report: Dict[str, Any]) -> str:
    """Write the JSON artefact under shared/outputs/goose/ and return its path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    return OUTPUT_FILE


def run() -> Dict[str, Any]:
    """Generate the report from the live data layer and write the artefact."""
    report = build_report()
    path = write_artifact(report)
    report = dict(report)
    report["artefact_path"] = path
    print(json.dumps(report, indent=2, sort_keys=True))
    print(render_text(report))
    return report


try:  # FastAPI surface is optional -- the module works standalone.
    from fastapi import APIRouter, Depends

    router = APIRouter(prefix="/api", tags=["axis-label-index-anomaly"])

    @router.get("/axis-label-index-anomaly")
    def axis_label_index_anomaly(db: Session = Depends(get_session)) -> dict:
        """Label_index integrity report for the stored axis scores."""
        return build_report(session=db)
except Exception:  # pragma: no cover - fastapi always present in deploy
    router = None


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", label_index=0,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                          label="STRONG", label_index=1,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv1", axis_name="capability_breadth",
                          label="BROAD", label_index=2,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=4, server_id="srv1", axis_name="data_sensitivity",
                          label="CRITICAL", label_index=3,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=5, server_id="srv1", axis_name="network_egress",
                          label="EXTERNAL", label_index=4,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=6, server_id="srv1", axis_name="maintainer_trust",
                          label="ESTABLISHED", label_index=5,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=7, server_id="srv1", axis_name="exploit_surface",
                          label="MODERATE", label_index=6,
                          model_version="v3.0_40974559"))
    s.commit(); s.close()

    # (a) complete server: 7 axes, label_index 0..6 -> no anomalies
    complete = [
        {"server_id": "srvA", "axis_name": ax, "label": "X", "label_index": i}
        for i, ax in enumerate(VALID_AXES)
    ]
    rep_a = analyse_axis_labels(complete)
    assert rep_a["anomalies"] == [], rep_a
    assert rep_a["checked"] == 7, rep_a
    assert rep_a["servers"] == 1, rep_a

    # (b) duplicated label_index -> anomaly detected
    dup = [
        {"server_id": "srvB", "axis_name": "overall_risk", "label": "HIGH", "label_index": 0},
        {"server_id": "srvB", "axis_name": "auth_strength", "label": "STRONG", "label_index": 0},
        {"server_id": "srvB", "axis_name": "capability_breadth", "label": "BROAD", "label_index": 1},
        {"server_id": "srvB", "axis_name": "data_sensitivity", "label": "CRITICAL", "label_index": 2},
        {"server_id": "srvB", "axis_name": "network_egress", "label": "EXTERNAL", "label_index": 3},
        {"server_id": "srvB", "axis_name": "maintainer_trust", "label": "ESTABLISHED", "label_index": 4},
        {"server_id": "srvB", "axis_name": "exploit_surface", "label": "MODERATE", "label_index": 5},
    ]
    rep_b = analyse_axis_labels(dup)
    issues_b = {(a["server_id"], a["issue"]) for a in rep_b["anomalies"]}
    assert ("srvB", ISSUE_DUPLICATE_INDEX) in issues_b, rep_b
    dup_anomaly = [a for a in rep_b["anomalies"] if a["issue"] == ISSUE_DUPLICATE_INDEX][0]
    assert dup_anomaly["axes"] == ["auth_strength", "overall_risk"], dup_anomaly
    assert rep_b["servers_affected"] == 1, rep_b

    # (c) missing 2 axes -> anomaly detected
    missing = [
        {"server_id": "srvC", "axis_name": ax, "label": "X", "label_index": i}
        for i, ax in enumerate(VALID_AXES[:5])
    ]
    rep_c = analyse_axis_labels(missing)
    issues_c = {(a["server_id"], a["issue"]) for a in rep_c["anomalies"]}
    assert ("srvC", ISSUE_MISSING_AXES) in issues_c, rep_c
    miss_anomaly = [a for a in rep_c["anomalies"] if a["issue"] == ISSUE_MISSING_AXES][0]
    assert miss_anomaly["axes"] == ["exploit_surface", "maintainer_trust"], miss_anomaly

    # (d) edge case: empty input is not an error
    rep_d = analyse_axis_labels([])
    assert rep_d["anomalies"] == [], rep_d
    assert rep_d["checked"] == 0, rep_d

    # (e) the same analysis over rows read from the real data layer
    report_db = build_report(session=s)
    assert report_db["checked"] == 7, report_db
    assert report_db["anomalies"] == [], report_db
    assert "generated_at" in report_db, report_db
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
    r = c.get("/api/axis-label-index-anomaly")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["checked"] == 7, j
    assert j["anomalies"] == [], j
    s.close()

    print("PASS")
