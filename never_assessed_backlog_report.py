"""never_assessed_backlog_report.py -- how much scoring work is left?

Reads the MCP server registry (app Postgres table `mcp_server_registry`) through
the canonical app DB seam (`from app.db import get_session`) and summarises the
never-assessed backlog plus the stale-assessed tail, so an operator can see how
much scoring work remains.

Read-only: no writes, no network, no duckdb. Import has no side effects.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


@contextmanager
def _session_scope() -> Iterator[Session]:
    """One session from the app DB dependency (closed on exit)."""
    gen = get_session()
    session = next(gen)
    try:
        yield session
    finally:
        gen.close()


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def build_report(db: Session, days: int = 30) -> Dict:
    """Core computation against an existing session."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()

    never_assessed = db.execute(
        select(func.count()).select_from(McpServerRegistry)
        .where(McpServerRegistry.last_assessed.is_(None))
    ).scalar_one()

    by_source: Dict[str, int] = {}
    for source, count in db.execute(
        select(McpServerRegistry.registry_source, func.count())
        .where(McpServerRegistry.last_assessed.is_(None))
        .group_by(McpServerRegistry.registry_source)
    ).all():
        by_source[str(source or "unknown")] = int(count)

    stale_assessed = db.execute(
        select(func.count()).select_from(McpServerRegistry)
        .where(McpServerRegistry.last_assessed.is_not(None),
               McpServerRegistry.last_assessed < cutoff)
    ).scalar_one()

    oldest = db.execute(select(func.min(McpServerRegistry.first_seen))).scalar()

    return {
        "total_servers": int(total or 0),
        "never_assessed": int(never_assessed or 0),
        "never_assessed_by_source": by_source,
        "stale_assessed": int(stale_assessed or 0),
        "oldest_first_seen": _iso(oldest),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_backlog_report(days: int = 30) -> Dict:
    """Public entry point: summarise the never-assessed backlog."""
    with _session_scope() as db:
        return build_report(db, days=days)


def write_report(report: Dict, path: str) -> str:
    """Write the report as JSON; returns the written path."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
    return path


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via the app DB seam
    import tempfile
    from datetime import datetime as _dt

    import app.db as _app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    now = _dt.now(timezone.utc)

    s = TS()
    s.add(McpServerRegistry(server_id="srv-never-npm", name="npm never assessed",
                            registry_source="npm", first_seen=now, last_seen=now))
    s.add(McpServerRegistry(server_id="srv-never-pypi", name="pypi never assessed",
                            registry_source="pypi", first_seen=now, last_seen=now))
    s.add(McpServerRegistry(server_id="srv-done", name="already assessed",
                            registry_source="npm", first_seen=now, last_seen=now,
                            last_assessed=now, risk_tier="MEDIUM"))
    s.commit()
    s.close()

    # point the canonical app DB dependency at the in-memory store for the test
    _app_db.SessionLocal = TS

    rep = build_backlog_report()
    assert rep["never_assessed"] == 2, rep
    assert "npm" in rep["never_assessed_by_source"], rep
    assert rep["never_assessed_by_source"]["npm"] == 1, rep
    assert rep["never_assessed_by_source"]["pypi"] == 1, rep
    assert rep["total_servers"] == 3, rep
    assert rep["stale_assessed"] == 0, rep
    assert rep["oldest_first_seen"] is not None, rep
    assert rep["generated_at"], rep

    with tempfile.TemporaryDirectory() as td:
        out = write_report(rep, os.path.join(td, "backlog.json"))
        assert os.path.exists(out), out
        with open(out, encoding="utf-8") as fh:
            back = json.load(fh)
        assert len(back) == len(rep), (back, rep)
        assert back["never_assessed"] == 2, back

    print("PASS")
