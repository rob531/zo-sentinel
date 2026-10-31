"""axis_escalation_ledger_report.py -- analyst-facing escalation ledger (Markdown).

Renders the last N days of ESCALATED axis decisions as a Markdown table for
pasting into analyst briefings.

Reads the authoritative APP tables `mcp_llm_axis_scores` (McpLlmAxisScore) JOIN
`mcp_server_registry` (McpServerRegistry) via the REAL app data layer
(app.db SQLAlchemy session) -- never a local engine/model and never the stale
:8772 copy of the app tables.

Pure part (stdlib only): render_markdown(rows).
Impure part: fetch_escalations(db, days) reads Postgres (read-only session);
build_report(days, rows=None) wires the two together.
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api/reports", tags=["reports"])

COLUMNS = ("server_id", "server_name", "axis_name", "p_top", "p_critical",
           "p_danger", "escalated_to", "scored_at", "risk_tier")

_GATE_AXES = ("overall_risk", "maintainer_trust")


# ---------------------------------------------------------------- helpers


def _parse_dt(value: Any) -> Optional[datetime]:
    """Coerce a datetime / ISO string into an aware UTC datetime (None if absent)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _cell(value: Any) -> str:
    """Markdown-safe cell value."""
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text or "0"
    return (str(value).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def _fmt_ts(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.isoformat() if dt else "-"


# ---------------------------------------------------------------- data access


def _axis_labels(db: Session, server_ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
    """{server_id: {axis_name: label}} for the trust-gate axes."""
    if not server_ids:
        return {}
    stmt = select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
                  McpLlmAxisScore.label).where(
        McpLlmAxisScore.server_id.in_(list(server_ids)),
        McpLlmAxisScore.axis_name.in_(_GATE_AXES),
    )
    out: Dict[str, Dict[str, str]] = {}
    for sid, axis, label in db.execute(stmt).all():
        if label:
            out.setdefault(sid, {})[axis] = label
    return out


def fetch_escalations(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Read the escalated axis decisions from the authoritative APP tables."""
    days = max(0, int(days))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    stmt = (
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
               McpLlmAxisScore.p_top, McpLlmAxisScore.p_critical,
               McpLlmAxisScore.p_danger, McpLlmAxisScore.escalated_to,
               McpLlmAxisScore.scored_at,
               McpServerRegistry.name, McpServerRegistry.url,
               McpServerRegistry.risk_tier)
        .join(McpServerRegistry,
              McpServerRegistry.server_id == McpLlmAxisScore.server_id, isouter=True)
        .where(McpLlmAxisScore.escalated.is_(True),
               McpLlmAxisScore.scored_at >= cutoff)
    )
    raw = db.execute(stmt).mappings().all()
    labels = _axis_labels(db, [r["server_id"] for r in raw])

    rows: List[Dict[str, Any]] = []
    for r in raw:
        server_id = r["server_id"]
        gate = trust_gate(r["url"], r["name"], labels.get(server_id, {}))
        published = gate.get("published_overall_risk")
        rows.append({
            "server_id": server_id,
            "server_name": r["name"],
            "axis_name": r["axis_name"],
            "p_top": r["p_top"],
            "p_critical": r["p_critical"],
            "p_danger": r["p_danger"],
            "escalated_to": r["escalated_to"],
            "scored_at": r["scored_at"],
            "risk_tier": published or r["risk_tier"],
        })
    return rows


# ---------------------------------------------------------------- pure core


def render_markdown(rows: List[Dict[str, Any]]) -> str:
    """Render escalation rows as a Markdown ledger table (newest first)."""
    rows = list(rows or [])
    ordered = sorted(rows, key=lambda r: (_parse_dt(r.get("scored_at")) is not None,
                                          _parse_dt(r.get("scored_at")) or datetime.min.replace(
                                              tzinfo=timezone.utc)), reverse=True)

    lines = [
        "# Axis Escalation Ledger",
        "",
        "| server_id | server_name | axis_name | p_top | p_critical | p_danger | "
        "escalated_to | scored_at | risk_tier |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in ordered:
        lines.append("| " + " | ".join([
            _cell(row.get("server_id")),
            _cell(row.get("server_name")),
            _cell(row.get("axis_name")),
            _cell(row.get("p_top")),
            _cell(row.get("p_critical")),
            _cell(row.get("p_danger")),
            _cell(row.get("escalated_to")),
            _fmt_ts(row.get("scored_at")),
            _cell(row.get("risk_tier")),
        ]) + " |")

    counts = Counter(str(r.get("escalated_to") or "unspecified")
                     for r in ordered)
    if counts:
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    else:
        summary = "no escalations"
    lines.extend(["", f"Summary: {summary}"])
    return "\n".join(lines)


def build_report(days: int = 30, rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """Escalation ledger for the last `days` days as Markdown.

    `rows` short-circuits the DB read; when None the rows are fetched from the
    real app session (read-only).
    """
    if rows is None:
        with _session_scope() as session:
            rows = fetch_escalations(session, days)
    return render_markdown(rows)


@contextmanager
def _session_scope():
    """One real app session (read-only use), closed on exit."""
    provider = get_session()
    session = next(provider)
    try:
        yield session
    finally:
        session.close()


@router.get("/axis-escalation-ledger")
def axis_escalation_ledger(days: int = 30,
                            db: Session = Depends(get_session)) -> str:
    """Escalation ledger (Markdown) for the last N days."""
    return render_markdown(fetch_escalations(db, days))


# ---------------------------------------------------------------- entrypoint


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Axis escalation ledger report")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default="", help="optional path for the Markdown report")
    args = parser.parse_args(argv)

    from app.db import SessionLocal
    session = SessionLocal()
    try:
        rows = fetch_escalations(session, args.days)
    finally:
        session.close()

    text = render_markdown(rows)
    if args.out:
        path = os.path.abspath(args.out)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


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
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            risk_tier="HIGH"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="maintainer_trust",
                          label="ESTABLISHED", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv1", axis_name="data_sensitivity",
                          label="CRITICAL", model_version="v3.0_40974559",
                          p_top=0.91, p_critical=0.4, p_danger=0.2,
                          escalated=True, escalated_to="critical",
                          scored_at=now - timedelta(days=2)))
    s.add(McpLlmAxisScore(id=4, server_id="srv1", axis_name="network_egress",
                          label="EXTERNAL", model_version="v3.0_40974559",
                          p_top=0.4, escalated=True, escalated_to="high",
                          scored_at=now - timedelta(days=5)))
    s.add(McpLlmAxisScore(id=5, server_id="srv1", axis_name="exploit_surface",
                          label="MODERATE", model_version="v3.0_40974559",
                          p_top=0.2, escalated=True, escalated_to="critical",
                          scored_at=now - timedelta(days=400)))
    s.commit()
    s.close()

    # --- pure-function acceptance (fixed synthetic rows) ---
    synth = [
        {"server_id": "s1", "server_name": "Alpha", "axis_name": "data_sensitivity",
         "p_top": 0.97, "p_critical": 0.5, "p_danger": 0.1,
         "escalated_to": "critical", "scored_at": now - timedelta(days=1),
         "risk_tier": "HIGH"},
        {"server_id": "s2", "server_name": "Beta", "axis_name": "network_egress",
         "p_top": 0.88, "p_critical": 0.2, "p_danger": 0.3,
         "escalated_to": "critical", "scored_at": now - timedelta(days=3),
         "risk_tier": "MEDIUM"},
        {"server_id": "s3", "server_name": None, "axis_name": "exploit_surface",
         "p_top": 0.61, "p_critical": 0.0, "p_danger": 0.2,
         "escalated_to": "high", "scored_at": now - timedelta(days=5),
         "risk_tier": "LOW"},
    ]
    md = build_report(days=30, rows=synth)
    assert isinstance(md, str) and md, md
    assert "| server_id |" in md, md
    for sid in ("s1", "s2", "s3"):
        assert sid in md, md
    assert "critical" in md, md
    assert md.index("s1") < md.index("s2") < md.index("s3"), md  # scored_at DESC

    empty = build_report(days=30, rows=[])
    assert isinstance(empty, str) and empty, empty
    assert "| server_id |" in empty, empty
    assert "no escalations" in empty, empty
    assert "| s1" not in empty, empty

    # --- endpoint over the REAL data layer (SQLite override of get_session) ---
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
    r = c.get("/api/reports/axis-escalation-ledger?days=30")
    assert r.status_code == 200, r.text
    body = r.text
    assert "| server_id |" in body, body
    assert "critical" in body, body
    assert body.count("| srv1 |") == 2, body   # the 400-day-old row is outside the window

    print("PASS")
