"""score_dispute_aging_ledger_report.py -- analyst-facing report over the open
score-dispute backlog and its aging.

Reads the authoritative APP table `mcp_score_disputes` (McpScoreDispute) via the
REAL app data layer (app.db SQLAlchemy session) -- never a local engine/model and
never the stale :8772 copy of the app tables.

Pure part (stdlib only): summarize_disputes(rows) / build_aging_ledger(rows).
Impure part: fetch_disputes(db) reads Postgres; main() writes an optional JSON
report. No DB writes, no network inside the pure functions.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute
from verdict_breakdown_api import get_principal, Principal

router = APIRouter(prefix="/api/reports", tags=["reports"])

DEFAULT_BUCKETS = (0, 7, 30, 90)
RESOLVED_STATUSES = frozenset({"resolved", "approved", "rejected", "closed", "withdrawn"})


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


def _age_days(created_at: Any, now: Optional[datetime] = None) -> Optional[float]:
    dt = _parse_dt(created_at)
    if dt is None:
        return None
    ref = now or datetime.now(timezone.utc)
    return (ref - dt).total_seconds() / 86400.0


def _is_open(row: Dict[str, Any]) -> bool:
    status = str(row.get("status") or "").strip().lower()
    return status not in RESOLVED_STATUSES


# ---------------------------------------------------------------- pure core


def build_aging_ledger(rows: List[Dict[str, Any]],
                       buckets: Sequence[int] = DEFAULT_BUCKETS) -> List[Dict[str, Any]]:
    """Aging histogram of the OPEN disputes.

    buckets are the lower edges (in days) of consecutive bands; the last band is
    open-ended. Returns one dict per band: label, min_age_days, max_age_days, count.
    """
    edges = sorted({int(b) for b in buckets})
    if not edges:
        edges = list(DEFAULT_BUCKETS)
    now = datetime.now(timezone.utc)

    counts = [0] * len(edges)
    for row in rows or []:
        if not _is_open(row):
            continue
        age = _age_days(row.get("created_at"), now)
        if age is None:
            continue
        age = max(0.0, age)
        placed = False
        for i, edge in enumerate(edges):
            upper = edges[i + 1] if i + 1 < len(edges) else None
            if age >= edge and (upper is None or age < upper):
                counts[i] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1

    ledger: List[Dict[str, Any]] = []
    for i, edge in enumerate(edges):
        upper = edges[i + 1] if i + 1 < len(edges) else None
        label = f"{edge}-{upper - 1}" if upper is not None else f"{edge}+"
        ledger.append({
            "label": label,
            "min_age_days": int(edge),
            "max_age_days": (int(upper) - 1) if upper is not None else None,
            "count": int(counts[i]),
        })
    return ledger


def summarize_disputes(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Backlog summary: totals by status / reason category, aging bands, oldest open age."""
    rows = list(rows or [])
    by_status = Counter(str(r.get("status") or "unknown") for r in rows)
    by_reason = Counter(str(r.get("reason_category") or "unknown") for r in rows)

    now = datetime.now(timezone.utc)
    ages = []
    for r in rows:
        if not _is_open(r):
            continue
        age = _age_days(r.get("created_at"), now)
        if age is not None:
            ages.append(max(0.0, age))

    return {
        "total": len(rows),
        "by_status": dict(by_status),
        "by_reason_category": dict(by_reason),
        "aging_buckets": build_aging_ledger(rows),
        "oldest_open_age_days": float(max(ages)) if ages else 0.0,
    }


# ---------------------------------------------------------------- data access


def fetch_disputes(db: Session, limit: int = 5000) -> List[Dict[str, Any]]:
    """Read the dispute backlog from the authoritative APP table via the app session."""
    limit = max(1, min(int(limit), 20000))
    stmt = select(
        McpScoreDispute.id,
        McpScoreDispute.server_id,
        McpScoreDispute.submitted_by,
        McpScoreDispute.proposed_overall_risk,
        McpScoreDispute.proposed_axes,
        McpScoreDispute.reason_category,
        McpScoreDispute.explanation,
        McpScoreDispute.status,
        McpScoreDispute.admin_note,
        McpScoreDispute.created_at,
        McpScoreDispute.resolved_at,
    ).order_by(McpScoreDispute.created_at.desc()).limit(limit)
    rows = db.execute(stmt).mappings().all()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["proposed_axes"] = d.get("proposed_axes") or {}
        out.append(d)
    return out


class DisputeAgingReport(BaseModel):
    total: int
    by_status: Dict[str, int]
    by_reason_category: Dict[str, int]
    aging_buckets: List[Dict[str, Any]]
    oldest_open_age_days: float


@router.get("/dispute-aging")
def dispute_aging_report(db: Session = Depends(get_session),
                         principal: Principal = Depends(get_principal)) -> dict:
    """Aging ledger + backlog summary for analyst review."""
    rows = fetch_disputes(db)
    return summarize_disputes(rows)


# ---------------------------------------------------------------- entrypoint


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Score-dispute aging ledger report")
    parser.add_argument("--out", default="", help="optional path for the JSON report")
    args = parser.parse_args(argv)

    from app.db import SessionLocal
    session = SessionLocal()
    try:
        rows = fetch_disputes(session)
    finally:
        session.close()

    report = summarize_disputes(rows)
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
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
    s.add(McpScoreDispute(id=1, server_id="srv1", submitted_by="u1",
                          proposed_overall_risk="MEDIUM",
                          proposed_axes={"maintainer_trust": "ESTABLISHED"},
                          reason_category="false_positive_overrated",
                          explanation="official maintainer, not actually high risk",
                          status="pending",
                          created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    s.add(McpScoreDispute(id=2, server_id="srv2", submitted_by="u2",
                          proposed_overall_risk="LOW",
                          proposed_axes={},
                          reason_category="other",
                          explanation="re-scored after a new release",
                          status="pending",
                          created_at=datetime(2026, 2, 1, tzinfo=timezone.utc)))
    s.add(McpScoreDispute(id=3, server_id="srv3", submitted_by="u3",
                          proposed_overall_risk="HIGH",
                          proposed_axes={},
                          reason_category="underrated_actual_risk",
                          explanation="genuinely risky server",
                          status="resolved",
                          created_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
                          resolved_at=datetime(2025, 12, 20, tzinfo=timezone.utc)))
    s.add(McpScoreDispute(id=4, server_id="srv4", submitted_by="u4",
                          proposed_overall_risk="LOW",
                          proposed_axes={},
                          reason_category="outdated_assessment",
                          explanation="stale assessment, no resolution yet",
                          status="pending",
                          created_at=datetime(2026, 3, 1, tzinfo=timezone.utc)))
    s.commit()
    s.close()

    # --- pure-function acceptance (fixed synthetic rows) ---
    now = datetime.now(timezone.utc)
    synth = [
        {"id": 1, "server_id": "s1", "submitted_by": "u1",
         "proposed_overall_risk": "MEDIUM", "proposed_axes": {}, "reason_category": "other",
         "explanation": "x", "status": "pending", "admin_note": None,
         "created_at": now - timedelta(days=45),
         "resolved_at": None},
        {"id": 2, "server_id": "s2", "submitted_by": "u2",
         "proposed_overall_risk": "LOW", "proposed_axes": {}, "reason_category": "other",
         "explanation": "x", "status": "pending", "admin_note": None,
         "created_at": now - timedelta(days=2),
         "resolved_at": None},
        {"id": 3, "server_id": "s3", "submitted_by": "u3",
         "proposed_overall_risk": "HIGH", "proposed_axes": {}, "reason_category": "other",
         "explanation": "x", "status": "resolved", "admin_note": "ok",
         "created_at": now - timedelta(days=60),
         "resolved_at": now - timedelta(days=50)},
        {"id": 4, "server_id": "s4", "submitted_by": "u4",
         "proposed_overall_risk": "LOW", "proposed_axes": {}, "reason_category": "other",
         "explanation": "x", "status": "pending", "admin_note": None,
         "created_at": now - timedelta(days=10),
         "resolved_at": None},
    ]
    summary = summarize_disputes(synth)
    assert summary["total"] == 4, summary
    assert sum(summary["by_status"].values()) == 4, summary
    assert summary["oldest_open_age_days"] >= 30, summary
    assert all(b["count"] >= 0 for b in summary["aging_buckets"]), summary
    assert sum(b["count"] for b in summary["aging_buckets"]) == 3, summary

    ledger = build_aging_ledger(synth)
    assert [b["label"] for b in ledger] == ["0-6", "7-29", "30-89", "90+"], ledger
    assert ledger[0]["count"] == 1 and ledger[2]["count"] == 1, ledger

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
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="t", role="admin")
    c = TestClient(app)
    r = c.get("/api/reports/dispute-aging")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["total"] == 4, j
    assert j["by_status"].get("pending") == 3, j
    assert j["by_status"].get("resolved") == 1, j
    assert j["oldest_open_age_days"] >= 30, j
    assert all(b["count"] >= 0 for b in j["aging_buckets"]), j

    print("PASS")
