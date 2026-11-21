"""app3t_unauth_access_source_report.py -- per-source breakdown of MCP servers with
recorded unauthenticated access.

Reads the isolated live-probe table mcp_unauth_access (app plane) through the REAL
app data layer (app.db / app.models) and reports, per source, how many servers were
probed and how many were seen serving unauthenticated access.

Pure formatting (build_rows_report) is kept separate from the fetch so it is
unit-testable without a database.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session  # noqa: F401  (real data-layer seam)
from app.models import McpServerRegistry, McpUnauthAccess  # noqa: F401

DEFAULT_DAYS = 30

_TRUE_TOKENS = {"OPEN", "OPEN_EXEC", "OPEN_BENIGN", "TRUE", "YES", "1",
                "UNSAFE", "EXPOSED", "UNAUTH", "UNAUTHENTICATED"}
_FALSE_TOKENS = {"GATED", "UNREACHABLE", "NONE", "SAFE", "FALSE", "NO", "0", ""}
_TRUE_KEYS = ("unauth", "unauthenticated", "is_unauth", "unauth_access", "open",
              "exposed", "accessible")


def _coerce_flag(value: Any) -> bool:
    """Normalise the unauth_access value to a boolean-ish exposure flag.

    The column is a free-form JSON/string field, so no fixed shape is assumed:
    booleans, JSON objects, JSON scalars and plain strings are all handled.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, dict):
        for key in _TRUE_KEYS:
            if key in value:
                return _coerce_flag(value[key])
        return False
    if isinstance(value, (list, tuple)):
        return any(_coerce_flag(v) for v in value)
    if not isinstance(value, str):
        return False

    text = value.strip()
    if not text:
        return False
    if text[0] in "{[\"":
        try:
            return _coerce_flag(json.loads(text))
        except (TypeError, ValueError):
            pass
    token = text.upper()
    if token in _FALSE_TOKENS:
        return False
    if token in _TRUE_TOKENS:
        return True
    return token.startswith("OPEN")


def _source_of(row: dict) -> str:
    source = row.get("source")
    if source is None:
        return "unknown"
    text = str(source).strip()
    return text or "unknown"


def _server_of(row: dict) -> str:
    return str(row.get("server_id") or "")


def build_rows_report(rows: Iterable[dict], days: int = DEFAULT_DAYS) -> dict:
    """Pure formatting: group rows by source and compute the unauthenticated rate.

    rows: dicts with keys server_id, source, unauth_access.
    """
    servers_by_source: dict[str, set[str]] = {}
    unauth_by_source: dict[str, set[str]] = {}

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        source = _source_of(row)
        server_id = _server_of(row)
        servers_by_source.setdefault(source, set()).add(server_id)
        if _coerce_flag(row.get("unauth_access")):
            unauth_by_source.setdefault(source, set()).add(server_id)

    sources = []
    for source in sorted(servers_by_source):
        total = len(servers_by_source[source])
        unauth = len(unauth_by_source.get(source, set()))
        rate = round(unauth / total, 4) if total else 0.0
        sources.append({
            "source": source,
            "total": total,
            "unauth": unauth,
            "unauth_rate": rate,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": int(days),
        "sources": sources,
    }


def fetch_rows(db: Session, days: int = DEFAULT_DAYS) -> list[dict]:
    """Read the mcp_unauth_access rows updated inside the `days` window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, int(days)))
    stmt = select(
        McpUnauthAccess.server_id,
        McpUnauthAccess.source,
        McpUnauthAccess.unauth_access,
        McpUnauthAccess.updated_at,
    ).where(McpUnauthAccess.updated_at >= cutoff)
    rows = db.execute(stmt).all()
    return [
        {
            "server_id": r[0],
            "source": r[1],
            "unauth_access": r[2],
            "updated_at": r[3].isoformat() if hasattr(r[3], "isoformat") else r[3],
        }
        for r in rows
    ]


def build_report(days: int = DEFAULT_DAYS) -> dict:
    """Per-source breakdown of recorded unauthenticated access (read-only)."""
    db = SessionLocal()
    try:
        rows = fetch_rows(db, days)
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[unauth-source-report] read failed: {exc}", file=sys.stderr)
        rows = []
    finally:
        db.close()
    return build_rows_report(rows, days)


if __name__ == "__main__":
    # -- pure formatting self-test: three fake row sets -------------------
    all_unauth_rows = [
        {"server_id": "s1", "source": "src_all", "unauth_access": "OPEN_EXEC"},
        {"server_id": "s2", "source": "src_all", "unauth_access": "OPEN_BENIGN"},
    ]
    none_unauth_rows = [
        {"server_id": "s3", "source": "src_none", "unauth_access": "GATED"},
        {"server_id": "s4", "source": "src_none", "unauth_access": None},
    ]
    mixed_rows = [
        {"server_id": "s5", "source": "src_mixed", "unauth_access": "OPEN"},
        {"server_id": "s6", "source": "src_mixed", "unauth_access": "GATED"},
    ]

    report = build_rows_report(all_unauth_rows + none_unauth_rows + mixed_rows, 30)
    assert len(report["sources"]) == 3, report
    by_source = {s["source"]: s for s in report["sources"]}
    assert by_source["src_all"]["unauth_rate"] == 1.0, report
    assert by_source["src_none"]["unauth_rate"] == 0.0, report
    assert by_source["src_mixed"]["unauth_rate"] == 0.5, report
    assert by_source["src_all"]["total"] == 2 and by_source["src_all"]["unauth"] == 2, report
    assert by_source["src_none"]["total"] == 2 and by_source["src_none"]["unauth"] == 0, report
    assert report["days"] == 30 and report["generated_at"], report
    assert build_rows_report([], 7)["sources"] == []

    # -- flag normalisation edge cases ------------------------------------
    assert _coerce_flag(None) is False
    assert _coerce_flag("") is False
    assert _coerce_flag("GATED") is False
    assert _coerce_flag("OPEN") is True
    assert _coerce_flag(True) is True
    assert _coerce_flag('{"unauth": true}') is True
    assert _coerce_flag('{"unauth": false}') is False

    # -- real data layer: in-memory SQLite session ------------------------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base = McpUnauthAccess.metadata
    Base.create_all(eng, tables=[McpUnauthAccess.__table__,
                                 McpServerRegistry.__table__])
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    now = datetime.now(timezone.utc)
    s.add(McpUnauthAccess(server_id="s1", source="src_all",
                          unauth_access="OPEN_EXEC", updated_at=now))
    s.add(McpUnauthAccess(server_id="s2", source="src_none",
                          unauth_access="GATED", updated_at=now))
    s.add(McpUnauthAccess(server_id="s3", source="src_all",
                          unauth_access="GATED",
                          updated_at=now - timedelta(days=90)))
    s.commit()

    live = build_rows_report(fetch_rows(s, 30), 30)
    live_sources = {x["source"]: x for x in live["sources"]}
    assert "src_all" in live_sources and "src_none" in live_sources, live
    assert live_sources["src_all"]["unauth_rate"] == 1.0, live
    assert live_sources["src_none"]["unauth_rate"] == 0.0, live
    # the 90-day-old row is outside the window
    assert live_sources["src_all"]["total"] == 1, live
    s.close()

    print("PASS")
