"""axis_label_index_drift_report.py -- SFT axis-label drift report generator.

Flags rows in mcp_llm_axis_scores whose stored `label_index` contradicts the
canonical label ladder for that axis, i.e. the class index the SFT head emitted
no longer matches the label text it was emitted with (label-ladder drift).

Read-only: no writes to any table, no network calls, no HTTP route. Data comes
from the REAL app data layer (app.db / app.models) via `get_session`.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


# Canonical per-axis label ladders, VERBATIM from the cross-repo contract
# schemas/risk_axis_mapping_v1.json (mirrored in tools/rescore/score_validity.py).
# Order is significant: index == labels.index(label).
AXIS_LABEL_LADDERS: Dict[str, Tuple[str, ...]] = {
    "overall_risk": ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
    "auth_strength": ("STRONG", "MODERATE", "WEAK", "UNKNOWN"),
    "capability_breadth": ("NARROW", "MODERATE", "BROAD", "UNKNOWN"),
    "data_sensitivity": ("PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN"),
    "network_egress": ("NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN"),
    "maintainer_trust": ("ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR",
                          "SUSPICIOUS"),
    "exploit_surface": ("MINIMAL", "LIMITED", "MODERATE", "BROAD", "UNKNOWN"),
}

MIN_DAYS = 1
MAX_DAYS = 365


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


def _cutoff(days: int) -> datetime:
    # scored_at is a naive TIMESTAMP column -> compare against naive UTC.
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _normalise(label: Optional[str]) -> str:
    if label is None:
        return ""
    return str(label).strip().upper().replace("-", "_").replace(" ", "_")


def _expected_index(axis_name: str, label: Optional[str]) -> Optional[int]:
    ladder = AXIS_LABEL_LADDERS.get(axis_name)
    if not ladder:
        return None
    normalised = _normalise(label)
    if not normalised:
        return None
    try:
        return ladder.index(normalised)
    except ValueError:
        return None


def _build_report(session: Session, days: int) -> Dict[str, Any]:
    if not isinstance(days, int) or isinstance(days, bool):
        raise ValueError("days must be an int")
    if days < MIN_DAYS or days > MAX_DAYS:
        raise ValueError(f"days must be between {MIN_DAYS} and {MAX_DAYS}")

    cutoff = _cutoff(days)

    rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()

    anomalies: List[Dict[str, Any]] = []
    servers: set = set()

    for server_id, axis_name, label, label_index in rows:
        expected = _expected_index(axis_name, label)
        if expected is None or label_index is None:
            continue
        if int(label_index) == expected:
            continue
        anomalies.append({
            "server_id": str(server_id),
            "axis_name": str(axis_name),
            "label": str(label),
            "label_index": int(label_index),
            "expected_index": int(expected),
        })
        servers.add(str(server_id))

    anomalies.sort(key=lambda a: (a["server_id"], a["axis_name"], a["label"]))

    return {
        "days": int(days),
        "checked_rows": len(rows),
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "servers_affected": len(servers),
    }


def build_report(days: int = 30, session: Optional[Session] = None) -> Dict[str, Any]:
    """Report of axis rows whose label_index contradicts the canonical ladder."""
    if session is not None:
        return _build_report(session, days)
    with _session_scope() as s:
        return _build_report(s, days)


def render_text(report: Dict[str, Any]) -> str:
    """Plain-text rendering of a build_report() result."""
    anomalies = list(report.get("anomalies") or [])
    lines: List[str] = []
    lines.append("MCP LLM AXIS SCORES -- AXIS LABEL INDEX DRIFT REPORT")
    lines.append("=" * 60)
    lines.append(f"days             : {report.get('days', '')}")
    lines.append(f"checked_rows     : {report.get('checked_rows', 0)}")
    lines.append(f"anomaly_count    : {report.get('anomaly_count', 0)}")
    lines.append(f"servers_affected : {report.get('servers_affected', 0)}")
    lines.append("")
    lines.append("anomalies")
    lines.append("-" * 60)
    if anomalies:
        for item in anomalies:
            lines.append(
                "{server_id} | {axis_name} | {label} | label_index={label_index} "
                "| expected_index={expected_index}".format(
                    server_id=item.get("server_id", ""),
                    axis_name=item.get("axis_name", ""),
                    label=item.get("label", ""),
                    label_index=item.get("label_index", ""),
                    expected_index=item.get("expected_index", ""),
                )
            )
    else:
        lines.append("(none)")
    return "\n".join(lines)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    # Three axis rows for ONE server: two consistent label_index values, one inverted.
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", label_index=2,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="auth_strength",
                          label="STRONG", label_index=0,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv1", axis_name="data_sensitivity",
                          label="CRITICAL", label_index=0,
                          model_version="v3.0_40974559"))
    s.commit()

    report = build_report(days=30, session=s)
    assert report["checked_rows"] == 3, report
    assert report["anomaly_count"] == 1, report
    assert report["servers_affected"] == 1, report
    assert report["days"] == 30, report
    anomaly = report["anomalies"][0]
    assert anomaly["server_id"] == "srv1", anomaly
    assert anomaly["axis_name"] == "data_sensitivity", anomaly
    assert anomaly["label"] == "CRITICAL", anomaly
    assert anomaly["label_index"] == 0, anomaly
    assert anomaly["expected_index"] == 3, anomaly

    txt = render_text(report)
    assert "anomalies" in txt, txt
    assert "data_sensitivity" in txt, txt
    assert "srv1" in txt, txt

    # edge case: a clean server raises no anomalies
    s.add(McpLlmAxisScore(id=4, server_id="srv2", axis_name="exploit_surface",
                          label="MODERATE", label_index=2,
                          model_version="v3.0_40974559"))
    s.commit()
    report2 = build_report(days=30, session=s)
    assert report2["checked_rows"] == 4, report2
    assert report2["anomaly_count"] == 1, report2
    assert report2["servers_affected"] == 1, report2

    # edge case: out-of-range window rejected
    for bad_days in (0, 366, -1):
        try:
            build_report(days=bad_days, session=s)
        except ValueError:
            pass
        else:
            raise AssertionError(f"days={bad_days} must raise ValueError")

    app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    @app.get("/axis-label-index-drift-report")
    def _drift_report(days: int = 30, session: Session = Depends(get_session)):
        return build_report(days=days, session=session)

    c = TestClient(app)
    r = c.get("/axis-label-index-drift-report?days=30")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["checked_rows"] == 4, j
    assert j["anomaly_count"] == 1, j
    assert j["servers_affected"] == 1, j
    s.close()

    print("PASS")
