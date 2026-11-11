"""mcp_llm_axis_scores_orphan_server_report.py -- orphan-score report generator.

Finds MCP servers that carry LLM axis scores (mcp_llm_axis_scores) but are missing
from the registry (mcp_server_registry) -- a data-integrity gap between the scoring
pipeline and the registry.

Read-only: no HTTP route, no DB writes, no network. Data comes from the REAL app
data layer (app.db / app.models).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


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


def _build_report(session: Session, days: int) -> dict[str, Any]:
    if days < 0:
        raise ValueError("days must be non-negative")

    cutoff = _cutoff(days)

    score_rows = session.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name).where(
            McpLlmAxisScore.scored_at >= cutoff
        )
    ).all()

    registry_ids = {
        sid for (sid,) in session.execute(
            select(McpServerRegistry.server_id)
        ).all()
    }

    orphan_ids: set[str] = set()
    by_axis: dict[str, int] = {}
    orphan_score_rows = 0

    for server_id, axis_name in score_rows:
        if server_id in registry_ids:
            continue
        orphan_ids.add(server_id)
        orphan_score_rows += 1
        key = axis_name or ""
        by_axis[key] = by_axis.get(key, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": int(days),
        "orphan_server_ids": sorted(orphan_ids),
        "orphan_score_rows": orphan_score_rows,
        "by_axis": by_axis,
    }


def build_report(days: int = 30, session: Optional[Session] = None) -> dict[str, Any]:
    """Report of scored-but-unregistered MCP servers over the last `days` days."""
    if session is not None:
        return _build_report(session, days)
    with _session_scope() as s:
        return _build_report(s, days)


def render_text(report: dict) -> str:
    """Fixed-width text rendering of a build_report() result."""
    lines: list[str] = []
    lines.append("MCP LLM AXIS SCORES -- ORPHAN SERVER REPORT")
    lines.append("=" * 60)
    lines.append(f"generated_at      : {report.get('generated_at', '')}")
    lines.append(f"window_days       : {report.get('window_days', '')}")
    lines.append(f"orphan_score_rows : {report.get('orphan_score_rows', 0)}")

    orphan_ids = list(report.get("orphan_server_ids") or [])
    lines.append("")
    lines.append("orphan_server_ids")
    lines.append("-" * 60)
    if orphan_ids:
        for sid in orphan_ids:
            lines.append(f"{sid}")
    else:
        lines.append("(none)")

    by_axis = report.get("by_axis") or {}
    lines.append("")
    lines.append("by_axis")
    lines.append("-" * 60)
    if by_axis:
        width = max(len(k) for k in by_axis)
        for axis in sorted(by_axis):
            lines.append(f"{axis.ljust(width)}  {by_axis[axis]}")
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
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit"))
    _i = 0
    for sid in ("srv1", "srv2"):
        for ax, lbl in (("overall_risk", "HIGH"), ("auth_strength", "STRONG"),
                        ("capability_breadth", "BROAD"),
                        ("data_sensitivity", "CRITICAL"),
                        ("network_egress", "EXTERNAL"),
                        ("maintainer_trust", "ESTABLISHED"),
                        ("exploit_surface", "MODERATE")):
            _i += 1
            s.add(McpLlmAxisScore(id=_i, server_id=sid, axis_name=ax, label=lbl,
                                  model_version="v3.0_40974559"))
    s.commit(); s.close()

    app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    @app.get("/orphan-report")
    def _orphan_report(days: int = 30, session: Session = Depends(get_session)):
        return build_report(days=days, session=session)

    c = TestClient(app)
    r = c.get("/orphan-report")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["orphan_server_ids"] == ["srv2"], j
    assert j["orphan_score_rows"] == 7, j
    assert j["window_days"] == 30, j
    assert j["by_axis"].get("overall_risk") == 1, j

    txt = render_text(j)
    assert "orphan_server_ids" in txt, txt
    assert "srv2" in txt, txt

    # edge case: empty window (days=0) yields no orphan rows
    r0 = c.get("/orphan-report?days=0")
    assert r0.status_code == 200, r0.text
    j0 = r0.json()
    assert j0["orphan_server_ids"] == [], j0
    assert j0["orphan_score_rows"] == 0, j0
    assert "orphan_server_ids" in render_text(j0)

    # edge case: negative window rejected
    try:
        build_report(days=-1, session=next(_override_session()))
    except ValueError:
        pass
    else:
        raise AssertionError("days=-1 must raise ValueError")

    print("PASS")
