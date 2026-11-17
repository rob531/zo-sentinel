"""dispute_resolution_cycle_report.py -- deterministic cycle-time report for MCP
score disputes.

Reads the authoritative APP table `mcp_score_disputes` (McpScoreDispute) through
the REAL app data layer (app.db SQLAlchemy session + app.models) -- never a local
engine/model and never the stale :8772 copy of the app tables.

Pure part (stdlib only): _median / _percentile / build_rows / render_markdown.
Impure part: _load_rows reads Postgres through the app session. No DB writes, no
network, no duckdb import.

Output:
    {"generated_at": iso8601,
     "rows": [{"submitted_by": str, "resolved": int,
               "median_days": float, "p90_days": float}],
     "totals": {...},
     "markdown": str}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from verdict_breakdown_api import Principal, get_principal

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Statuses that close the dispute loop (a cycle time exists once resolved).
RESOLVED_STATUSES = frozenset({"resolved", "approved", "rejected", "closed"})

MARKDOWN_HEADER = "| submitter | resolved | median_days | p90_days |"


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


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Field access that tolerates ORM rows, mappings and plain dicts."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _median(values: Sequence[float]) -> float:
    """Median of a numeric sequence (0.0 for an empty sequence)."""
    data = sorted(float(v) for v in values)
    n = len(data)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def _percentile(values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile (no numpy/pandas).

    p in [0, 100]. For a sorted sample the percentile sits at index
    k = (n - 1) * pct / 100 and is interpolated between the two neighbours.
    """
    data = sorted(float(v) for v in values)
    n = len(data)
    if n == 0:
        return 0.0
    if n == 1:
        return data[0]
    k = (n - 1) * (pct / 100.0)
    lo = int(k)
    if lo >= n - 1:
        return data[n - 1]
    frac = k - lo
    return data[lo] + (data[lo + 1] - data[lo]) * frac


def _duration_days(created_at: Any, resolved_at: Any) -> Optional[float]:
    """Cycle time in days between two timestamps (None if either is missing)."""
    start = _parse_dt(created_at)
    end = _parse_dt(resolved_at)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 86400.0


def _is_resolved(row: Any) -> bool:
    status = str(_row_get(row, "status") or "").strip().lower()
    if status in RESOLVED_STATUSES:
        return True
    return _row_get(row, "resolved_at") is not None


# ---------------------------------------------------------------- pure core


def build_rows(rows: Sequence[Any]) -> List[Dict[str, Any]]:
    """Per-submitter cycle-time statistics.

    Each input row needs: submitted_by, created_at, resolved_at, status.
    A dispute counts as resolved when its status is closed OR it carries a
    resolved_at timestamp; the cycle time is only measured when BOTH
    timestamps are present.
    """
    durations: Dict[str, List[float]] = {}
    resolved_counts: Dict[str, int] = {}
    order: List[str] = []

    for row in rows or []:
        submitter = _row_get(row, "submitted_by")
        if submitter is None:
            continue
        submitter = str(submitter)
        if submitter not in resolved_counts:
            resolved_counts[submitter] = 0
            durations[submitter] = []
            order.append(submitter)
        if not _is_resolved(row):
            continue
        resolved_counts[submitter] += 1
        days = _duration_days(_row_get(row, "created_at"), _row_get(row, "resolved_at"))
        if days is not None:
            durations[submitter].append(days)

    out: List[Dict[str, Any]] = []
    for submitter in order:
        values = durations[submitter]
        out.append({
            "submitted_by": submitter,
            "resolved": int(resolved_counts[submitter]),
            "median_days": round(_median(values), 4),
            "p90_days": round(_percentile(values, 90.0), 4),
        })
    out.sort(key=lambda r: (-r["resolved"], r["submitted_by"]))
    return out


def build_totals(rows: Sequence[Any], per_submitter: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Portfolio-level totals across every dispute row."""
    all_durations: List[float] = []
    resolved = 0
    for row in rows or []:
        if not _is_resolved(row):
            continue
        resolved += 1
        days = _duration_days(_row_get(row, "created_at"), _row_get(row, "resolved_at"))
        if days is not None:
            all_durations.append(days)
    total_rows = len(list(rows or []))
    return {
        "disputes": total_rows,
        "resolved": int(resolved),
        "unresolved": int(total_rows - resolved),
        "submitters": len(per_submitter),
        "median_days": round(_median(all_durations), 4),
        "p90_days": round(_percentile(all_durations, 90.0), 4),
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report as a deterministic Markdown table."""
    lines: List[str] = []
    lines.append("# Dispute Resolution Cycle-Time Report")
    lines.append("")
    lines.append(f"Generated at: {report.get('generated_at')}")
    lines.append("")
    lines.append(MARKDOWN_HEADER)
    lines.append("|---|---|---:|---:|")
    for row in report.get("rows", []) or []:
        lines.append(
            "| {submitted_by} | {resolved} | {median_days:.2f} | {p90_days:.2f} |".format(**row)
        )
    totals = report.get("totals", {}) or {}
    lines.append("")
    lines.append(
        "Totals: disputes={disputes} resolved={resolved} unresolved={unresolved} "
        "submitters={submitters} median_days={median_days:.2f} p90_days={p90_days:.2f}".format(
            disputes=totals.get("disputes", 0),
            resolved=totals.get("resolved", 0),
            unresolved=totals.get("unresolved", 0),
            submitters=totals.get("submitters", 0),
            median_days=float(totals.get("median_days", 0.0)),
            p90_days=float(totals.get("p90_days", 0.0)),
        )
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- data access


def _query_rows(db: Session) -> List[Dict[str, Any]]:
    """Read the dispute rows through the app SQLAlchemy session."""
    stmt = select(
        McpScoreDispute.id,
        McpScoreDispute.submitted_by,
        McpScoreDispute.status,
        McpScoreDispute.reason_category,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).order_by(McpScoreDispute.id)
    out: List[Dict[str, Any]] = []
    for r in db.execute(stmt).mappings().all():
        d = dict(r)
        out.append({
            "id": d.get("id"),
            "submitted_by": d.get("submitted_by"),
            "status": d.get("status"),
            "reason_category": d.get("reason_category"),
            "created_at": d.get("created_at"),
            "resolved_at": d.get("resolved_at"),
        })
    return out


def _load_rows(source: Union[Session, Callable[[], Any], None]) -> List[Dict[str, Any]]:
    """Resolve the dispute rows from a session, a callable or the app session.

    source may be:
      * None             -> a fresh app.db SessionLocal session (read-only)
      * a Session-like   -> queried directly
      * a callable       -> called; if it returns a list of rows it is used as-is,
                            if it returns a session-like it is queried.
    """
    if source is None:
        from app.db import SessionLocal
        session = SessionLocal()
        try:
            return _query_rows(session)
        finally:
            session.close()

    if callable(source) and not hasattr(source, "execute"):
        result = source()
        if isinstance(result, (list, tuple)):
            return list(result)
        if hasattr(result, "execute"):
            return _query_rows(result)
        raise TypeError("callable must return dispute rows or a session")

    if hasattr(source, "execute"):
        return _query_rows(source)
    raise TypeError("source must be None, a session, or a callable returning rows")


# ---------------------------------------------------------------- report


def build_report(db_session_or_callable: Union[Session, Callable[[], Any], None] = None) -> Dict[str, Any]:
    """Build the dispute cycle-time report.

    Returns {"generated_at": iso8601, "rows": [...], "totals": {...},
    "markdown": str}. Read-only: no writes, no network.
    """
    rows = _load_rows(db_session_or_callable)
    per_submitter = build_rows(rows)
    totals = build_totals(rows, per_submitter)
    report: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": per_submitter,
        "totals": totals,
    }
    report["markdown"] = render_markdown(report)
    return report


# ---------------------------------------------------------------- API


class SubmitterCycle(BaseModel):
    submitted_by: str
    resolved: int
    median_days: float
    p90_days: float


class CycleTotals(BaseModel):
    disputes: int
    resolved: int
    unresolved: int
    submitters: int
    median_days: float
    p90_days: float


class DisputeCycleReport(BaseModel):
    generated_at: str
    rows: List[SubmitterCycle]
    totals: CycleTotals


@router.get("/dispute-resolution-cycle", response_model=DisputeCycleReport)
def dispute_resolution_cycle(db: Session = Depends(get_session),
                             principal: Principal = Depends(get_principal)) -> dict:
    """Cycle-time report for MCP score disputes (JSON)."""
    return build_report(db)


@router.get("/dispute-resolution-cycle.md", response_class=PlainTextResponse)
def dispute_resolution_cycle_markdown(db: Session = Depends(get_session),
                                      principal: Principal = Depends(get_principal)) -> str:
    """Cycle-time report for MCP score disputes (Markdown)."""
    return build_report(db)["markdown"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print the report as JSON + Markdown from the real app data layer."""
    report = build_report()
    print(json.dumps({k: v for k, v in report.items() if k != "markdown"},
                     indent=2, sort_keys=True, default=str))
    print(report["markdown"])
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite via dependency override
    from datetime import timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    now = datetime.now(timezone.utc)

    # --- pure-function acceptance: 5 synthetic disputes with known offsets ---
    synthetic = [
        {"id": 1, "submitted_by": "u1", "status": "resolved",
         "reason_category": "false_positive_overrated",
         "created_at": now - timedelta(days=11), "resolved_at": now - timedelta(days=10)},
        {"id": 2, "submitted_by": "u1", "status": "resolved",
         "reason_category": "false_positive_overrated",
         "created_at": now - timedelta(days=13), "resolved_at": now - timedelta(days=10)},
        {"id": 3, "submitted_by": "u1", "status": "resolved",
         "reason_category": "other",
         "created_at": now - timedelta(days=15), "resolved_at": now - timedelta(days=10)},
        {"id": 4, "submitted_by": "u2", "status": "pending",
         "reason_category": "underrated_actual_risk",
         "created_at": now - timedelta(days=5), "resolved_at": None},
        {"id": 5, "submitted_by": "u2", "status": "resolved",
         "reason_category": "other",
         "created_at": now - timedelta(days=9), "resolved_at": now - timedelta(days=8)},
    ]
    report = build_report(lambda: synthetic)
    assert "generated_at" in report, report
    assert isinstance(report["rows"], list) and report["rows"], report
    assert "totals" in report and "markdown" in report, report

    by_submitter = {r["submitted_by"]: r for r in report["rows"]}
    u1 = by_submitter["u1"]
    assert u1["resolved"] == 3, u1
    # offsets are exactly 1, 3 and 5 days -> median 3.0
    assert abs(u1["median_days"] - 3.0) < 1e-6, u1
    # 90th percentile of [1, 3, 5] -> 3 + 0.8 * (5 - 3) = 4.6
    assert abs(u1["p90_days"] - 4.6) < 1e-6, u1
    for row in report["rows"]:
        assert row["p90_days"] >= row["median_days"], row
    u2 = by_submitter["u2"]
    assert u2["resolved"] == 1, u2
    assert abs(u2["median_days"] - 1.0) < 1e-6, u2

    totals = report["totals"]
    assert totals["disputes"] == 5, totals
    assert totals["resolved"] == 4, totals
    assert totals["unresolved"] == 1, totals
    assert totals["submitters"] == 2, totals

    assert MARKDOWN_HEADER in report["markdown"], report["markdown"]
    assert "| u1 |" in report["markdown"], report["markdown"]
    assert "# Dispute Resolution Cycle-Time Report" in report["markdown"], report["markdown"]

    # edge case: no disputes at all
    empty = build_report(lambda: [])
    assert empty["rows"] == [], empty
    assert empty["totals"]["disputes"] == 0, empty
    assert MARKDOWN_HEADER in empty["markdown"], empty["markdown"]

    # --- end-to-end through the router on an in-memory SQLite app ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpScoreDispute(id=1, server_id="srv1", submitted_by="u1",
                          proposed_overall_risk="MEDIUM",
                          proposed_axes={"maintainer_trust": "ESTABLISHED"},
                          reason_category="false_positive_overrated",
                          explanation="official maintainer, not actually high risk",
                          status="resolved",
                          created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          resolved_at=datetime(2026, 1, 11, tzinfo=timezone.utc)))
    s.add(McpScoreDispute(id=2, server_id="srv2", submitted_by="u2",
                          proposed_overall_risk="LOW",
                          proposed_axes={},
                          reason_category="other",
                          explanation="still open",
                          status="pending",
                          created_at=datetime(2026, 2, 1, tzinfo=timezone.utc)))
    s.commit()
    s.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")
    c = TestClient(app)
    r = c.get("/api/reports/dispute-resolution-cycle")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["totals"]["disputes"] == 2, j
    assert j["totals"]["resolved"] == 1, j
    rows = {row["submitted_by"]: row for row in j["rows"]}
    assert abs(rows["u1"]["median_days"] - 10.0) < 1e-6, j
    assert rows["u1"]["resolved"] == 1, j
    rm = c.get("/api/reports/dispute-resolution-cycle.md")
    assert rm.status_code == 200, rm.text
    assert MARKDOWN_HEADER in rm.text, rm.text
    assert "u1" in rm.text, rm.text

    print("PASS")
