"""axis_escalation_report.py -- standalone escalation report over the SFT axis scores.

Renders "which axes tripped escalation, to what, and for which servers" from the
REAL app data layer: McpLlmAxisScore (mcp_llm_axis_scores) LEFT JOINed to
McpServerRegistry (mcp_server_registry) for the server label.

Read-only: no writes, no network, no import-time I/O. Importable as
`axis_escalation_report`.
"""
# deps: sqlalchemy
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

DEFAULT_WINDOW_DAYS = 30
MAX_WINDOW_DAYS = 365


def _clamp_days(window_days: int) -> int:
    try:
        value = int(window_days)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return max(1, min(MAX_WINDOW_DAYS, value))


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


@contextmanager
def _session_scope(db: Optional[Session]) -> Iterator[Session]:
    """Use the caller's session when given, else open one via the app dependency."""
    if db is not None:
        yield db
        return
    gen = get_session()
    session: Session = next(gen)
    try:
        yield session
    finally:
        session.close()
        try:
            next(gen)
        except StopIteration:
            pass


def build_axis_escalation_report(
    db_session: Optional[Session] = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> dict:
    """Build the escalation report.

    Returns:
        {"generated_at": iso8601, "window_days": int, "total_escalations": int,
         "by_axis": [{"axis_name", "count", "escalated_to"}],
         "rows": [{"server_id", "axis_name", "p_top", "escalated_to", "scored_at",
                   "server_name", "trusted"}]}
    """
    days = _clamp_days(window_days)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    with _session_scope(db_session) as session:
        raw = session.execute(
            select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.escalated_to,
                McpLlmAxisScore.scored_at,
                McpServerRegistry.name,
                McpServerRegistry.url,
            )
            .outerjoin(
                McpServerRegistry,
                McpServerRegistry.server_id == McpLlmAxisScore.server_id,
            )
            .where(
                McpLlmAxisScore.escalated.is_(True),
                McpLlmAxisScore.scored_at >= cutoff,
            )
        ).all()

    rows: List[dict] = []
    for server_id, axis_name, p_top, escalated_to, scored_at, name, url in raw:
        # trust_gate annotates verified publishers so an official server's
        # escalation is never read as a false positive against its maintainer.
        gate = trust_gate(url, name, {})
        rows.append({
            "server_id": str(server_id) if server_id is not None else None,
            "axis_name": str(axis_name),
            "p_top": float(p_top) if p_top is not None else None,
            "escalated_to": str(escalated_to) if escalated_to is not None else None,
            "scored_at": _iso(scored_at),
            "server_name": str(name) if name is not None else None,
            "trusted": bool(gate.get("trusted")),
        })

    rows.sort(key=lambda r: (str(r["axis_name"]), str(r["server_id"] or "")))

    counts: Counter = Counter()
    targets: dict = {}
    for row in rows:
        axis = row["axis_name"]
        counts[axis] += 1
        targets.setdefault(axis, Counter())[row["escalated_to"] or "unknown"] += 1

    by_axis: List[dict] = []
    for axis_name, count in sorted(counts.items(), key=lambda item: (item[0], -item[1])):
        target_counter = targets[axis_name]
        escalated_to = sorted(target_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        by_axis.append({
            "axis_name": axis_name,
            "count": int(count),
            "escalated_to": escalated_to,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "total_escalations": len(rows),
        "by_axis": by_axis,
        "rows": rows,
    }


def format_axis_escalation_report(report: dict) -> str:
    """Render a report dict as a plain-text table. Pure -- never touches the DB."""
    report = report or {}
    by_axis = report.get("by_axis") or []
    lines = [
        f"axis_escalation_report generated_at={report.get('generated_at')} "
        f"window_days={report.get('window_days')} "
        f"total_escalations={report.get('total_escalations')}",
        f"{'axis_name':<24}{'count':>6}  escalated_to",
    ]
    for entry in by_axis:
        lines.append(
            f"{str(entry.get('axis_name', '')):<24}"
            f"{int(entry.get('count', 0)):>6}  {entry.get('escalated_to') or ''}"
        )
    if not by_axis:
        lines.append("(no escalations in window)")
    return "\n".join(lines)


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TS()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(McpServerRegistry(server_id="srv1", name="Alpha MCP",
                                   url="https://example.com/alpha"))
    session.add(McpServerRegistry(server_id="srv2", name="Beta MCP",
                                   url="https://example.com/beta"))
    session.add(McpServerRegistry(server_id="srv3", name="Gamma MCP",
                                   url="https://example.com/gamma"))
    session.add(McpServerRegistry(server_id="srv4", name="Delta MCP",
                                   url="https://example.com/delta"))
    session.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="auth_strength",
                                label="WEAK", model_version="v3.0_40974559",
                                p_top=0.91, escalated=True, escalated_to="CRITICAL",
                                scored_at=now - timedelta(days=1)))
    session.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="auth_strength",
                                label="WEAK", model_version="v3.0_40974559",
                                p_top=0.77, escalated=True, escalated_to="CRITICAL",
                                scored_at=now - timedelta(days=2)))
    session.add(McpLlmAxisScore(id=3, server_id="srv3", axis_name="data_sensitivity",
                                label="HIGH", model_version="v3.0_40974559",
                                p_top=0.64, escalated=True, escalated_to="HIGH",
                                scored_at=now - timedelta(days=3)))
    session.add(McpLlmAxisScore(id=4, server_id="srv4", axis_name="network_egress",
                                label="EXTERNAL", model_version="v3.0_40974559",
                                p_top=0.21, escalated=False, escalated_to=None,
                                scored_at=now - timedelta(days=1)))
    session.commit()

    report = build_axis_escalation_report(session)
    assert report["total_escalations"] == 3, report
    assert len(report["by_axis"]) == 2, report
    axes = [entry["axis_name"] for entry in report["by_axis"]]
    assert "network_egress" not in axes, report
    assert axes == sorted(axes), report
    assert len(report["rows"]) == 3, report
    assert report["window_days"] == 30, report

    text = format_axis_escalation_report(report)
    assert isinstance(text, str), text
    assert "axis_name" in text, text
    for entry in report["by_axis"]:
        assert entry["axis_name"] in text, text

    empty = format_axis_escalation_report({"generated_at": "x", "window_days": 30,
                                           "total_escalations": 0, "by_axis": [],
                                           "rows": []})
    assert "axis_name" in empty, empty
    session.close()
    print("PASS")
