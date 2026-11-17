"""server_score_completeness_report.py -- coverage of the LLM axis-scoring pipeline.

Reports which registry servers carry all 7 risk axes, which are missing axes and
which carry stale scores. The report logic (build_report / render_markdown) is
stdlib-only and DB-free; the read-only router wires the REAL app data layer
(app.db / app.models) and serves the same report as JSON and Markdown.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api/report", tags=["report"])

VALID_AXES = ("overall_risk", "auth_strength", "capability_breadth",
              "data_sensitivity", "network_egress", "maintainer_trust",
              "exploit_surface")
STALE_AFTER_DAYS = 30
MAX_SAMPLES = 50


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _current_rows(rows: List[dict]) -> List[dict]:
    """Rows for a server's current model_version (the one most recently scored)."""
    if not rows:
        return []
    newest_ts: Optional[datetime] = None
    newest_mv: Optional[str] = None
    for row in rows:
        ts = _parse_ts(row.get("scored_at"))
        mv = row.get("model_version")
        if newest_ts is None or (ts is not None and ts > newest_ts):
            if ts is None and newest_ts is not None:
                continue
            newest_ts = ts
            newest_mv = mv
    if newest_mv is None:
        return list(rows)
    same = [r for r in rows if r.get("model_version") == newest_mv]
    return same or list(rows)


def build_report(registry_rows: List[dict], axis_rows: List[dict], *,
                 now: Optional[datetime] = None,
                 stale_after_days: int = STALE_AFTER_DAYS) -> Dict[str, Any]:
    """Measure axis-scoring completeness over the registry.

    registry_rows: dicts with server_id.
    axis_rows: dicts with server_id, axis_name, scored_at (ISO string), model_version.
    """
    now_utc = _parse_ts(now) or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=stale_after_days)

    server_ids: List[str] = []
    seen = set()
    for row in registry_rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("server_id")
        if sid is None or sid in seen:
            continue
        seen.add(sid)
        server_ids.append(sid)

    rows_by_server: Dict[str, List[dict]] = {}
    for row in axis_rows or []:
        if not isinstance(row, dict):
            continue
        sid = row.get("server_id")
        if sid is None:
            continue
        rows_by_server.setdefault(sid, []).append(row)

    fully = partial = unscored = stale = 0
    missing_counter: Counter = Counter()
    samples: List[dict] = []

    for sid in server_ids:
        rows = _current_rows(rows_by_server.get(sid, []))
        scored = {r.get("axis_name") for r in rows if r.get("axis_name") in VALID_AXES}
        missing = [a for a in VALID_AXES if a not in scored]
        for axis in missing:
            missing_counter[axis] += 1

        if not scored:
            unscored += 1
        elif not missing:
            fully += 1
        else:
            partial += 1
            if len(samples) < MAX_SAMPLES:
                samples.append({"server_id": sid, "missing": missing})

        stamps = [t for t in (_parse_ts(r.get("scored_at")) for r in rows) if t]
        if stamps and max(stamps) < cutoff:
            stale += 1

    total = len(server_ids)
    completeness_pct = round((fully / total) * 100, 2) if total else 0.0

    return {
        "servers_total": total,
        "servers_fully_scored": fully,
        "servers_partially_scored": partial,
        "servers_unscored": unscored,
        "completeness_pct": completeness_pct,
        "missing_axis_counts": {axis: int(missing_counter.get(axis, 0)) for axis in VALID_AXES},
        "partial_server_samples": samples,
        "servers_stale": stale,
        "stale_after_days": int(stale_after_days),
        "generated_at": now_utc.isoformat(),
    }


def render_markdown(report: dict) -> str:
    """Render the report dict as Markdown."""
    report = report or {}
    total = int(report.get("servers_total", 0) or 0)
    fully = int(report.get("servers_fully_scored", 0) or 0)
    partial = int(report.get("servers_partially_scored", 0) or 0)
    unscored = int(report.get("servers_unscored", 0) or 0)
    pct = report.get("completeness_pct", 0.0)
    stale = int(report.get("servers_stale", 0) or 0)

    lines: List[str] = []
    lines.append("# Server Score Completeness Report")
    lines.append("")
    lines.append(f"- Registry servers: **{total}**")
    lines.append(f"- Fully scored (7/7 axes): **{fully}**")
    lines.append(f"- Partially scored: **{partial}**")
    lines.append(f"- Unscored: **{unscored}**")
    lines.append(f"- Completeness: **{pct}%**")
    lines.append(f"- Stale scores: **{stale}**")
    lines.append("")
    lines.append("## Missing axis counts")
    lines.append("")
    lines.append("| Axis | Servers missing |")
    lines.append("| --- | --- |")
    counts = report.get("missing_axis_counts") or {}
    for axis in VALID_AXES:
        lines.append(f"| {axis} | {int(counts.get(axis, 0) or 0)} |")
    lines.append("")
    samples = report.get("partial_server_samples") or []
    lines.append("## Partially scored servers")
    lines.append("")
    if not samples:
        lines.append("_None._")
    else:
        lines.append("| Server | Missing axes |")
        lines.append("| --- | --- |")
        for sample in samples:
            sid = sample.get("server_id")
            missing = ", ".join(sample.get("missing") or [])
            lines.append(f"| {sid} | {missing} |")
    lines.append("")
    return "\n".join(lines)


class PartialServerSample(BaseModel):
    server_id: str
    missing: List[str]


class ScoreCompletenessReport(BaseModel):
    servers_total: int
    servers_fully_scored: int
    servers_partially_scored: int
    servers_unscored: int
    completeness_pct: float
    missing_axis_counts: Dict[str, int]
    partial_server_samples: List[PartialServerSample]
    servers_stale: int = 0
    stale_after_days: int = STALE_AFTER_DAYS
    generated_at: Optional[str] = None


def _load_rows(db: Session) -> (List[dict], List[dict]):
    registry_rows = [
        {"server_id": row[0]}
        for row in db.execute(select(McpServerRegistry.server_id)).all()
    ]
    axis_rows = [
        {
            "server_id": row.server_id,
            "axis_name": row.axis_name,
            "scored_at": row.scored_at.isoformat() if row.scored_at else None,
            "model_version": row.model_version,
        }
        for row in db.execute(select(McpLlmAxisScore)).scalars().all()
    ]
    return registry_rows, axis_rows


@router.get("/score-completeness", response_model=ScoreCompletenessReport)
def get_score_completeness(db: Session = Depends(get_session)) -> dict:
    """Axis-scoring completeness across the registry (JSON)."""
    registry_rows, axis_rows = _load_rows(db)
    return build_report(registry_rows, axis_rows)


@router.get("/score-completeness.md", response_class=PlainTextResponse)
def get_score_completeness_markdown(db: Session = Depends(get_session)) -> str:
    """Axis-scoring completeness across the registry (Markdown)."""
    registry_rows, axis_rows = _load_rows(db)
    return render_markdown(build_report(registry_rows, axis_rows))


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=1)).isoformat()
    older = (now - timedelta(days=400)).isoformat()

    registry_rows = [
        {"server_id": "srv_full"},
        {"server_id": "srv_partial"},
        {"server_id": "srv_none"},
    ]
    axis_rows = []
    for axis in VALID_AXES:
        axis_rows.append({"server_id": "srv_full", "axis_name": axis,
                          "scored_at": recent, "model_version": "v3.0_40974559"})
    for axis in ("overall_risk", "auth_strength"):
        axis_rows.append({"server_id": "srv_partial", "axis_name": axis,
                          "scored_at": recent, "model_version": "v3.0_40974559"})
    axis_rows.append({"server_id": "srv_partial", "axis_name": "network_egress",
                      "scored_at": recent, "model_version": "v2.0_old"})

    report = build_report(registry_rows, axis_rows, now=now)
    assert report["servers_total"] == 3, report
    assert report["servers_fully_scored"] == 1, report
    assert report["servers_partially_scored"] == 1, report
    assert report["servers_unscored"] == 1, report
    assert abs(report["completeness_pct"] - 33.33) < 0.01, report
    sample = report["partial_server_samples"][0]
    assert sample["server_id"] == "srv_partial", report
    assert len(sample["missing"]) == 5, report
    assert report["missing_axis_counts"]["exploit_surface"] == 2, report
    md = render_markdown(report)
    assert "Server Score Completeness Report" in md, md
    assert "srv_partial" in md, md

    # stale detection: a fully scored server whose scores aged out
    stale_rows = [{"server_id": "s", "axis_name": a, "scored_at": older,
                   "model_version": "v3.0_40974559"} for a in VALID_AXES]
    stale_report = build_report([{"server_id": "s"}], stale_rows, now=now)
    assert stale_report["servers_stale"] == 1, stale_report

    # edge case: empty registry
    empty = build_report([], [], now=now)
    assert empty["servers_total"] == 0 and empty["completeness_pct"] == 0.0, empty

    # end-to-end through the router on an in-memory SQLite app
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    for _i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                    ("capability_breadth", "BROAD"), ("data_sensitivity", "CRITICAL"),
                    ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                    ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=_i, server_id="srv1", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI(); app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/report/score-completeness"); assert r.status_code == 200, r.text
    j = r.json()
    assert j["servers_total"] == 1, j
    assert j["servers_fully_scored"] == 1, j
    assert j["completeness_pct"] == 100.0, j
    rm = c.get("/api/report/score-completeness.md"); assert rm.status_code == 200, rm.text
    assert "Server Score Completeness Report" in rm.text, rm.text
    print("PASS")
