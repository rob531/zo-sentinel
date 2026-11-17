"""advisory_link_confidence_drift_report.py -- audits how vuln_links.match_confidence
has drifted over time, and how many links carry low-confidence matches.

Pure part (stdlib only): build_link_confidence_drift(link_rows, advisory_rows, now)
returns the report dict.

DB-API part: build_link_confidence_drift_report(conn) reads the two authoritative
APP tables through a DB-API-style connection / cursor provider using parameterised
SQL (no DuckDB-only syntax -- it runs on Postgres).

App part: build_report(db=...) reads the SAME tables through the REAL app data
layer (`from app.db import get_session` / `from app.models import VulnAdvisory,
VulnLink`) and exposes a read-only router. No writes, no network, no CSV/JSON
inputs.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

router = APIRouter(prefix="/api/reports", tags=["reports"])

LOW_CONFIDENCE_THRESHOLD = 0.5
UNKNOWN_WINDOW_LABEL = "unknown"

_LINK_SQL = (
    "SELECT id, advisory_id, server_id, match_basis, match_value, "
    "match_confidence, linked_at FROM vuln_links"
)
_ADVISORY_SQL = (
    "SELECT id, feed, severity, ecosystem, package, published_at FROM vuln_advisories"
)


# ---------------------------------------------------------------- helpers


def _as_utc(value: Any) -> Optional[datetime]:
    """Parse a timestamp into an aware UTC datetime (None when unparsable)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _confidence(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _window_key(dt: Optional[datetime]) -> tuple:
    """(label, bucket_start) for a linked_at timestamp -- monthly windows."""
    if dt is None:
        return (UNKNOWN_WINDOW_LABEL, None)
    start = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (start.strftime("%Y-%m"), start)


def _avg(values: List[float]) -> Optional[float]:
    return round(fmean(values), 4) if values else None


# ---------------------------------------------------------------- pure core


def build_link_confidence_drift(link_rows: List[Dict[str, Any]],
                                 advisory_rows: List[Dict[str, Any]],
                                 now: Optional[datetime] = None) -> Dict[str, Any]:
    """Confidence-drift report over advisory->server links.

    `link_rows` dicts carry id, advisory_id, server_id, match_basis, match_value,
    match_confidence, linked_at. `advisory_rows` dicts carry id (+ advisory
    metadata). Inputs are never mutated.
    """
    reference = _as_utc(now) or datetime.now(timezone.utc)

    advisory_ids = set()
    for advisory in (advisory_rows or []):
        key = advisory.get("id")
        if key is not None:
            advisory_ids.add(key)

    buckets: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    basis_counts: Dict[str, int] = defaultdict(int)
    basis_conf: Dict[str, List[float]] = defaultdict(list)
    basis_order: List[str] = []

    total_links = 0
    low_confidence_count = 0
    orphan_link_count = 0
    confidences: List[float] = []
    unknown_timestamp_count = 0

    for link in (link_rows or []):
        total_links += 1
        confidence = _confidence(link.get("match_confidence"))
        if confidence is not None:
            confidences.append(confidence)
            if confidence < LOW_CONFIDENCE_THRESHOLD:
                low_confidence_count += 1

        if link.get("advisory_id") not in advisory_ids:
            orphan_link_count += 1

        basis = link.get("match_basis")
        basis_key = str(basis) if basis is not None else "unknown"
        if basis_key not in basis_counts:
            basis_order.append(basis_key)
        basis_counts[basis_key] += 1
        if confidence is not None:
            basis_conf[basis_key].append(confidence)

        dt = _as_utc(link.get("linked_at"))
        if dt is None:
            unknown_timestamp_count += 1
        label, bucket_start = _window_key(dt)
        if label not in buckets:
            buckets[label] = {
                "label": label,
                "bucket_start": bucket_start.isoformat() if bucket_start else None,
                "link_count": 0,
                "low_confidence_count": 0,
                "_conf": [],
            }
            order.append(label)
        bucket = buckets[label]
        bucket["link_count"] += 1
        if confidence is not None:
            bucket["_conf"].append(confidence)
            if confidence < LOW_CONFIDENCE_THRESHOLD:
                bucket["low_confidence_count"] += 1

    windows = []
    for label in order:
        bucket = buckets[label]
        conf = bucket.pop("_conf")
        bucket["avg_confidence"] = _avg(conf)
        windows.append(bucket)

    basis_breakdown = []
    for basis_key in basis_order:
        basis_breakdown.append({
            "match_basis": basis_key,
            "count": basis_counts[basis_key],
            "avg_confidence": _avg(basis_conf[basis_key]),
        })

    timed_windows = [w for w in windows if w["bucket_start"] is not None]
    first_avg = timed_windows[0]["avg_confidence"] if timed_windows else None
    last_avg = timed_windows[-1]["avg_confidence"] if timed_windows else None
    drift = None
    if first_avg is not None and last_avg is not None:
        drift = round(last_avg - first_avg, 4)

    summary = {
        "total_links": total_links,
        "total_advisories": len(advisory_ids),
        "low_confidence_count": low_confidence_count,
        "low_confidence_threshold": LOW_CONFIDENCE_THRESHOLD,
        "low_confidence_pct": (round(100.0 * low_confidence_count / total_links, 2)
                               if total_links else 0.0),
        "avg_confidence": _avg(confidences),
        "orphan_link_count": orphan_link_count,
        "window_count": len(windows),
        "basis_count": len(basis_breakdown),
        "unknown_timestamp_count": unknown_timestamp_count,
        "confidence_drift": drift,
        "drifting": bool(drift is not None and drift < 0),
    }

    return {
        "generated_at": reference.isoformat(),
        "windows": windows,
        "basis_breakdown": basis_breakdown,
        "orphan_link_count": orphan_link_count,
        "summary": summary,
    }


# ---------------------------------------------------------------- DB-API part


def _cursor(conn: Any) -> Any:
    """Accept a DB-API connection (has .cursor) or a cursor provider directly."""
    if hasattr(conn, "cursor"):
        return conn.cursor()
    return conn


def _fetch(conn: Any, sql: str) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    try:
        cur.execute(sql)
        rows = cur.fetchall()
    finally:
        close = getattr(cur, "close", None)
        if callable(close):
            close()
    columns = ("id", "advisory_id", "server_id", "match_basis", "match_value",
               "match_confidence", "linked_at")
    out: List[Dict[str, Any]] = []
    for row in rows:
        values = list(row)
        if isinstance(row, dict):
            out.append({k: row.get(k) for k in columns})
        else:
            out.append(dict(zip(columns, values)))
    return out


def _fetch_advisories(conn: Any) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    try:
        cur.execute(_ADVISORY_SQL)
        rows = cur.fetchall()
    finally:
        close = getattr(cur, "close", None)
        if callable(close):
            close()
    columns = ("id", "feed", "severity", "ecosystem", "package", "published_at")
    out: List[Dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append({k: row.get(k) for k in columns})
        else:
            out.append(dict(zip(columns, list(row))))
    return out


def build_link_confidence_drift_report(conn: Any,
                                       now: Optional[datetime] = None) -> Dict[str, Any]:
    """Build the drift report from a DB-API-style connection / cursor provider.

    Read-only, parameterised, Postgres-portable SQL. `conn` may be a connection
    (exposing .cursor()) or a cursor provider itself.
    """
    link_rows = _fetch(conn, _LINK_SQL)
    advisory_rows = _fetch_advisories(conn)
    return build_link_confidence_drift(link_rows, advisory_rows, now)


# ---------------------------------------------------------------- app part


def fetch_link_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the link side from the authoritative APP table (read-only)."""
    stmt = select(
        VulnLink.id,
        VulnLink.advisory_id,
        VulnLink.server_id,
        VulnLink.match_basis,
        VulnLink.match_value,
        VulnLink.match_confidence,
        VulnLink.linked_at,
    )
    return [
        {
            "id": r[0],
            "advisory_id": r[1],
            "server_id": r[2],
            "match_basis": r[3],
            "match_value": r[4],
            "match_confidence": r[5],
            "linked_at": r[6],
        }
        for r in db.execute(stmt).all()
    ]


def fetch_advisory_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the advisory side from the authoritative APP table (read-only)."""
    stmt = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnAdvisory.published_at,
    )
    return [
        {
            "id": r[0],
            "feed": r[1],
            "severity": r[2],
            "ecosystem": r[3],
            "package": r[4],
            "published_at": r[5],
        }
        for r in db.execute(stmt).all()
    ]


@contextmanager
def _session_scope():
    """One real app session (read-only use), closed on exit."""
    provider = get_session()
    session = next(provider)
    try:
        yield session
    finally:
        session.close()


def build_report(now: Optional[datetime] = None,
                 db: Optional[Session] = None) -> Dict[str, Any]:
    """Build the drift report from the real app tables (read-only)."""
    reference = now or datetime.now(timezone.utc)
    if db is not None:
        return build_link_confidence_drift(fetch_link_rows(db),
                                           fetch_advisory_rows(db), reference)
    with _session_scope() as session:
        return build_link_confidence_drift(fetch_link_rows(session),
                                           fetch_advisory_rows(session), reference)


@router.get("/advisory-link-confidence-drift")
def advisory_link_confidence_drift(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Confidence-drift + low-confidence audit for advisory->server links."""
    return build_report(db=db)


# ---------------------------------------------------------------- rendering


def render_text_report(report: Dict[str, Any]) -> str:
    """Human-readable rendering of the drift report."""
    lines = ["Advisory link confidence drift report",
             "generated_at: %s" % report.get("generated_at"),
             ""]
    summary = report.get("summary") or {}
    lines.append("Summary")
    lines.append("  total_links          : %s" % summary.get("total_links"))
    lines.append("  low_confidence_links : %s (threshold %s)" % (
        summary.get("low_confidence_count"), summary.get("low_confidence_threshold")))
    lines.append("  avg_confidence       : %s" % summary.get("avg_confidence"))
    lines.append("  orphan_links         : %s" % summary.get("orphan_link_count"))
    lines.append("  confidence_drift     : %s" % summary.get("confidence_drift"))
    lines.append("")
    lines.append("Windows")
    for window in report.get("windows") or []:
        lines.append("  %-8s start=%s links=%d avg=%s low=%d" % (
            window.get("label"), window.get("bucket_start"), window.get("link_count"),
            window.get("avg_confidence"), window.get("low_confidence_count")))
    lines.append("")
    lines.append("Match basis breakdown")
    for basis in report.get("basis_breakdown") or []:
        lines.append("  %-16s count=%d avg=%s" % (
            basis.get("match_basis"), basis.get("count"), basis.get("avg_confidence")))
    return "\n".join(lines)


def run() -> Dict[str, Any]:
    """Build the report from the real data layer and print it as text."""
    report = build_report()
    print(render_text_report(report))
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Advisory link confidence drift report")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.parse_args(argv)
    report = run()
    if argv is not None and "--json" in argv:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    import sqlite3

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)

    # --- fixture 1: DB-API path over an in-memory SQLite database ---
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE vuln_links (id INTEGER PRIMARY KEY, advisory_id TEXT, "
                "server_id TEXT, match_basis TEXT, match_value TEXT, "
                "match_confidence REAL, linked_at TEXT)")
    con.execute("CREATE TABLE vuln_advisories (id TEXT PRIMARY KEY, feed TEXT, "
                "severity TEXT, ecosystem TEXT, package TEXT, published_at TEXT)")
    con.execute("INSERT INTO vuln_advisories (id, feed, severity, ecosystem, package, "
                "published_at) VALUES (?,?,?,?,?,?)",
                ("CVE-2026-0001", "osv", "HIGH", "npm", "left-pad",
                 "2026-09-01T00:00:00+00:00"))
    con.execute("INSERT INTO vuln_advisories (id, feed, severity, ecosystem, package, "
                "published_at) VALUES (?,?,?,?,?,?)",
                ("CVE-2026-0002", "ghsa", "MEDIUM", "npm", "right-pad",
                 "2026-10-01T00:00:00+00:00"))
    seeds = [
        (1, "CVE-2026-0001", "srv_1", "package_exact", "left-pad", 0.9,
         "2026-09-05T12:00:00+00:00"),
        (2, "CVE-2026-0002", "srv_2", "repo_exact", "org/repo", 0.9,
         "2026-09-25T12:00:00+00:00"),
        (3, "CVE-2026-0001", "srv_3", "package_exact", "left-pad", 0.2,
         "2026-09-20T12:00:00+00:00"),
        (4, "CVE-4040-4040", "srv_4", "package_exact", "ghost-pad", 0.2,
         "2026-10-20T12:00:00+00:00"),
    ]
    con.executemany(
        "INSERT INTO vuln_links (id, advisory_id, server_id, match_basis, match_value, "
        "match_confidence, linked_at) VALUES (?,?,?,?,?,?,?)", seeds)
    con.commit()

    class _CursorAdapter:
        """Small cursor provider: the report only needs .cursor()/.execute()."""

        def __init__(self, connection):
            self._connection = connection

        def cursor(self):
            return self._connection.cursor()

    report = build_link_confidence_drift_report(_CursorAdapter(con), now=NOW)
    total = sum(w["link_count"] for w in report["windows"])
    assert total == 4, report
    assert report["summary"]["total_links"] == 4, report
    assert report["summary"]["low_confidence_count"] == 2, report
    assert report["orphan_link_count"] == 1, report
    distinct_bases = len({s[3] for s in seeds})
    assert len(report["basis_breakdown"]) == distinct_bases, report
    assert {b["match_basis"] for b in report["basis_breakdown"]} == \
        {"package_exact", "repo_exact"}, report
    assert len(report["windows"]) == 2, report
    assert report["windows"][0]["label"] == "2026-09", report
    assert report["windows"][1]["label"] == "2026-10", report
    assert report["summary"]["drifting"] is True, report
    assert "Advisory link confidence drift report" in render_text_report(report)
    con.close()

    # --- fixture 2: empty database -> zeroed report, no raise ---
    empty_con = sqlite3.connect(":memory:")
    empty_con.execute("CREATE TABLE vuln_links (id INTEGER PRIMARY KEY, advisory_id TEXT, "
                       "server_id TEXT, match_basis TEXT, match_value TEXT, "
                       "match_confidence REAL, linked_at TEXT)")
    empty_con.execute("CREATE TABLE vuln_advisories (id TEXT PRIMARY KEY, feed TEXT, "
                      "severity TEXT, ecosystem TEXT, package TEXT, published_at TEXT)")
    empty = build_link_confidence_drift_report(empty_con, now=NOW)
    assert empty["summary"]["total_links"] == 0, empty
    assert empty["windows"] == [], empty
    assert empty["basis_breakdown"] == [], empty
    assert empty["orphan_link_count"] == 0, empty
    empty_con.close()

    # --- real data layer path (SQLite session over the real models) ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(VulnAdvisory(id="CVE-2026-0001", feed="osv", severity="HIGH",
                       package="left-pad", source_url="https://osv.dev/CVE-2026-0001",
                       published_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                       fetched_at=datetime(2026, 10, 1, 1, tzinfo=timezone.utc)))
    s.add(VulnAdvisory(id="CVE-2026-0002", feed="ghsa", severity="LOW",
                       package="right-pad", source_url="https://github.com/advisories/0002",
                       published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                       fetched_at=datetime(2026, 9, 1, 1, tzinfo=timezone.utc)))
    s.add(VulnLink(id=1, advisory_id="CVE-2026-0001", server_id="srv_fresh",
                   match_basis="package_exact", match_value="left-pad",
                   match_confidence=0.9,
                   linked_at=datetime(2026, 10, 27, 12, tzinfo=timezone.utc)))
    s.add(VulnLink(id=2, advisory_id="CVE-2026-0002", server_id="srv_mid",
                   match_basis="repo_exact", match_value="org/repo",
                   match_confidence=0.2,
                   linked_at=datetime(2026, 9, 20, 12, tzinfo=timezone.utc)))
    s.add(VulnLink(id=3, advisory_id="CVE-4040-4040", server_id="srv_orphan",
                   match_basis="package_exact", match_value="ghost-pad",
                   match_confidence=0.2,
                   linked_at=datetime(2026, 10, 1, 12, tzinfo=timezone.utc)))
    s.commit()

    live = build_report(now=NOW, db=s)
    assert live["summary"]["total_links"] == 3, live
    assert live["summary"]["low_confidence_count"] == 2, live
    assert live["orphan_link_count"] == 1, live
    assert len(live["basis_breakdown"]) == 2, live

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
    r = c.get("/api/reports/advisory-link-confidence-drift")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["total_links"] == 3, body
    assert body["orphan_link_count"] == 1, body
    s.close()

    print("PASS")
