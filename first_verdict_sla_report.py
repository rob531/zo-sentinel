"""first_verdict_sla_report.py -- PRODUCT_SPEC section 4 freshness SLA report.

Measures: "New MCP must have a first verdict within 24h of first_seen".
Reads mcp_server_registry (server_id, first_seen, last_assessed, verdict,
risk_tier) through the REAL app data layer (app.db / app.models) -- never via
duckdb, never via file reads. Read-only: no writes, no network.

Usage:
    python3 first_verdict_sla_report.py            # markdown summary to stdout
    build_first_verdict_sla_report(days=30, out_path="report.json")
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select

from app.db import get_session
from app.models import McpServerRegistry

SLA_HOURS = 24.0

# Test seam: the __main__ self-test points this at the in-memory SQLite session
# factory. The production path uses the real app.db session dependency.
_session_provider = None  # type: Optional[Any]


@contextmanager
def _session_scope() -> Iterator[Any]:
    """Yield an app DB session (real: app.db.get_session; test: overridden)."""
    if _session_provider is not None:
        s = _session_provider()
        try:
            yield s
        finally:
            s.close()
        return
    gen = get_session()
    s = next(gen)
    try:
        yield s
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: Optional[datetime]) -> str:
    d = _as_utc(dt)
    return d.isoformat() if d else ""


def _fetch_rows(db: Any, days: int) -> List[Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stmt = (select(McpServerRegistry)
            .where(McpServerRegistry.first_seen.isnot(None))
            .order_by(McpServerRegistry.server_id))
    rows = db.execute(stmt).scalars().all()
    out: List[Any] = []
    for r in rows:
        fs = _as_utc(r.first_seen)
        if fs is None or fs < cutoff:
            continue
        out.append(r)
    return out


def render_markdown(report: dict) -> str:
    lines = [
        "# First-Verdict SLA Report",
        "",
        f"- window_days: {report['window_days']}",
        f"- total_servers: {report['total_servers']}",
        f"- with_verdict: {report['with_verdict']}",
        f"- breached: {report['breached']}",
        f"- breach_rate: {report['breach_rate']}",
        "",
        "| server_id | first_seen | last_assessed | hours_to_verdict |",
        "| --- | --- | --- | --- |",
    ]
    for b in report["breaches"]:
        lines.append(
            f"| {b['server_id']} | {b['first_seen']} | {b['last_assessed']} "
            f"| {b['hours_to_verdict']} |")
    if not report["breaches"]:
        lines.append("| _none_ | | | |")
    return "\n".join(lines)


def build_first_verdict_sla_report(days: int = 30,
                                    out_path: str | None = None) -> dict:
    """Compute the first-verdict SLA report over the last `days` days.

    A server counts as breached when its verdict timestamp (last_assessed) is
    more than SLA_HOURS (24h) after first_seen. Servers with no verdict yet are
    surfaced via with_verdict vs total_servers and are not scored as breaches
    (their SLA clock has not expired).
    """
    days = max(0, int(days))
    with _session_scope() as db:
        rows = _fetch_rows(db, days)

        total_servers = len(rows)
        with_verdict = 0
        breaches: List[Dict[str, Any]] = []
        for r in rows:
            fs = _as_utc(r.first_seen)
            la = _as_utc(r.last_assessed)
            if fs is None or la is None:
                continue
            with_verdict += 1
            hours = round((la - fs).total_seconds() / 3600.0, 2)
            if hours < 0:
                hours = 0.0
            if hours > SLA_HOURS:
                breaches.append({
                    "server_id": r.server_id,
                    "first_seen": _iso(fs),
                    "last_assessed": _iso(la),
                    "hours_to_verdict": hours,
                })

        breaches.sort(key=lambda b: b["hours_to_verdict"], reverse=True)
        breached = len(breaches)
        breach_rate = (breached / total_servers) if total_servers else 0.0

    report = {
        "window_days": days,
        "total_servers": total_servers,
        "with_verdict": with_verdict,
        "breached": breached,
        "breach_rate": breach_rate,
        "breaches": breaches,
    }

    print(render_markdown(report))
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=True)
    return report


if __name__ == "__main__":
    import os
    import tempfile

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)

    def _row(sid: str, hours_to_verdict: float, tier: str) -> McpServerRegistry:
        fs = now - timedelta(days=10)
        return McpServerRegistry(
            server_id=sid, name=f"Server {sid}", url=f"https://example.com/{sid}",
            registry_source="test", verdict="HIGH", risk_tier=tier,
            first_seen=fs, last_assessed=fs + timedelta(hours=hours_to_verdict),
        )

    s = TS()
    s.add(_row("srv-fast", 2.0, "LOW"))
    s.add(_row("srv-fast2", 23.5, "MEDIUM"))
    s.add(_row("srv-late", 30.0, "HIGH"))
    s.add(_row("srv-late2", 72.0, "CRITICAL"))
    s.commit()
    s.close()

    globals()["_session_provider"] = TS

    report = build_first_verdict_sla_report(days=30)
    assert report["total_servers"] == 4, report
    assert report["with_verdict"] == 4, report
    assert report["breached"] == 2, report
    assert report["breach_rate"] == 0.5, report
    assert report["window_days"] == 30, report
    hours = [b["hours_to_verdict"] for b in report["breaches"]]
    assert hours == sorted(hours, reverse=True), report
    assert {b["server_id"] for b in report["breaches"]} == {"srv-late", "srv-late2"}, report

    # out_path writes the same dict as JSON
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "sla.json")
        rep2 = build_first_verdict_sla_report(days=30, out_path=out)
        with open(out, encoding="utf-8") as fh:
            assert json.load(fh) == rep2, "out_path JSON must match the returned dict"

    print("PASS")
