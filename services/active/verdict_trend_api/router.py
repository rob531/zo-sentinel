# deps: fastapi, sqlalchemy, pydantic
"""verdict_trend_api -- daily verdict transition aggregation."""
from __future__ import annotations

import sys
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parents[3])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

GET /api/verdict/trend
  Returns per-day counts of verdict transitions (e.g. TRUSTED_GENERAL ->
  CAUTION_LIMITED) for MCP servers over a configurable lookback window.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app Postgres via get_session + SQLAlchemy ORM on
  mcp_llm_axis_scores (axis_name='overall_risk') joined with mcp_server_registry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

# Ensure repo root on sys.path so `app.db` / `app.models` resolve
_repo_root = str(Path(__file__).resolve().parents[3])
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict_trend_api"])


# --------------------------------------------------------------------------- #
# Pydantic response models
# --------------------------------------------------------------------------- #

class VerdictTransitionEntry(BaseModel):
    date: str
    from_verdict: str
    to_verdict: str
    count: int


class VerdictTrendResponse(BaseModel):
    days: int
    series: List[VerdictTransitionEntry]


# --------------------------------------------------------------------------- #
# SQLAlchemy ORM query (SQLite + Postgres portable)
# --------------------------------------------------------------------------- #

def _get_verdict_trend(session: Session, days: int) -> VerdictTrendResponse:
    """
    Detect verdict transitions using the overall_risk axis in mcp_llm_axis_scores.

    Algorithm (SQLite + Postgres portable, no raw SQL):
    1. Filter to overall_risk axis scores within the lookback window.
    2. Window-rank scores per server by scored_at DESC (rn=1 = most recent).
    3. Self-join consecutive ranks to detect (prev_label != curr_label).
    4. Aggregate by transition date and (from_verdict, to_verdict) pair.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Subquery: scores for the overall_risk axis within the window, ranked newest-first
    recent = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.label,
            McpLlmAxisScore.scored_at,
            func.row_number()
            .over(
                partition_by=McpLlmAxisScore.server_id,
                order_by=McpLlmAxisScore.scored_at.desc(),
            )
            .label("rn"),
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .subquery()
    )

    # Current score (rn=1)
    curr = (
        select(
            recent.c.server_id,
            recent.c.label.label("to_verdict"),
            func.date(recent.c.scored_at).label("transition_date"),
        )
        .where(recent.c.rn == 1)
        .subquery(name="curr_score")
    )

    # Previous score (rn=2)
    prev = (
        select(
            recent.c.server_id,
            recent.c.label.label("from_verdict"),
        )
        .where(recent.c.rn == 2)
        .subquery(name="prev_score")
    )

    # Detect transitions: same server, different labels
    transitions = select(
        curr.c.server_id,
        curr.c.transition_date,
        curr.c.to_verdict,
        prev.c.from_verdict,
    ).join(prev, curr.c.server_id == prev.c.server_id).where(
        curr.c.to_verdict != prev.c.from_verdict,
        curr.c.to_verdict.isnot(None),
        prev.c.from_verdict.isnot(None),
    )

    # Aggregate
    stmt = select(
        func.date(transitions.c.transition_date).label("date"),
        transitions.c.from_verdict,
        transitions.c.to_verdict,
        func.count().label("count"),
    ).group_by(
        func.date(transitions.c.transition_date),
        transitions.c.from_verdict,
        transitions.c.to_verdict,
    ).order_by(
        func.date(transitions.c.transition_date),
        transitions.c.from_verdict,
        transitions.c.to_verdict,
    )

    rows = session.execute(stmt).all()

    series = [
        VerdictTransitionEntry(
            date=str(row.date),
            from_verdict=str(row.from_verdict),
            to_verdict=str(row.to_verdict),
            count=row.count,
        )
        for row in rows
    ]

    return VerdictTrendResponse(days=days, series=series)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get("/verdict/trend", response_model=VerdictTrendResponse)
def get_verdict_trend(
    days: int = Query(default=7, ge=1, le=365, description="Lookback window in days"),
    session: Session = Depends(get_session),
) -> VerdictTrendResponse:
    """
    Return per-day verdict transition counts for the configured lookback window.

    Each entry represents how many servers changed from one verdict to another
    on a given date. Entries where no transition occurred are excluded.
    """
    return _get_verdict_trend(session, days)


# --------------------------------------------------------------------------- #
# Self-test (SQLite in-memory)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables matching the real schema column names/types
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(512),
                registry_source VARCHAR(64),
                url TEXT,
                description TEXT,
                trust_score FLOAT,
                verdict VARCHAR(64),
                verdict_reasoning TEXT,
                confidence FLOAT,
                last_assessed TIMESTAMP,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                risk_tier VARCHAR(32),
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128),
                axis_name VARCHAR(64),
                label VARCHAR(64),
                label_index INTEGER,
                probs TEXT,
                p_top FLOAT,
                p_critical FLOAT,
                p_danger FLOAT,
                escalated INTEGER DEFAULT 0,
                escalated_to VARCHAR(64),
                decision_rule_version VARCHAR(64),
                model_version VARCHAR(64),
                adapter_sha256 VARCHAR(64),
                scored_at TIMESTAMP
            )
        """))
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Seed test data
    now = datetime.utcnow()
    d1 = (now - timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0)
    d2 = (now - timedelta(days=2)).replace(hour=12, minute=0, second=0, microsecond=0)
    d3 = (now - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

    with engine.begin() as conn:
        # Servers
        conn.execute(text(
            "INSERT INTO mcp_server_registry (server_id, name, verdict) VALUES (:sid, :name, :verdict)"
        ), {"sid": "srv-1", "name": "Alpha", "verdict": "TRUSTED_GENERAL"})
        conn.execute(text(
            "INSERT INTO mcp_server_registry (server_id, name, verdict) VALUES (:sid, :name, :verdict)"
        ), {"sid": "srv-2", "name": "Beta", "verdict": "TRUSTED_GENERAL"})
        conn.execute(text(
            "INSERT INTO mcp_server_registry (server_id, name, verdict) VALUES (:sid, :name, :verdict)"
        ), {"sid": "srv-3", "name": "Gamma", "verdict": "TRUSTED_GENERAL"})

        # srv-1: day-3 TRUSTED -> day-2 CAUTION_LIMITED
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-1", "lbl": "TRUSTED_GENERAL", "ts": d1.isoformat()})
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-1", "lbl": "CAUTION_LIMITED", "ts": d2.isoformat()})

        # srv-2: day-2 TRUSTED -> day-1 CAUTION_LIMITED
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-2", "lbl": "TRUSTED_GENERAL", "ts": d2.isoformat()})
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-2", "lbl": "CAUTION_LIMITED", "ts": d3.isoformat()})

        # srv-3: stays TRUSTED_GENERAL throughout (no transition)
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-3", "lbl": "TRUSTED_GENERAL", "ts": d1.isoformat()})
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-3", "lbl": "TRUSTED_GENERAL", "ts": d2.isoformat()})
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'overall_risk', :lbl, :ts)"
        ), {"sid": "srv-3", "lbl": "TRUSTED_GENERAL", "ts": d3.isoformat()})

        # Additional axis (non-overall_risk) -- should be ignored
        conn.execute(text(
            "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, scored_at) VALUES (:sid, 'auth_strength', :lbl, :ts)"
        ), {"sid": "srv-1", "lbl": "WEAK", "ts": d2.isoformat()})

        conn.commit()

    def _override():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)

    # Test 1: basic happy path
    r = client.get("/api/verdict/trend?days=7")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()
    assert data["days"] == 7
    assert "series" in data
    assert isinstance(data["series"], list)

    # Test 2: we expect 2 transitions:
    #   day-2: srv-1 TRUSTED_GENERAL -> CAUTION_LIMITED
    #   day-1: srv-2 TRUSTED_GENERAL -> CAUTION_LIMITED
    by_pair = {(t["from_verdict"], t["to_verdict"]): t for t in data["series"]}
    key = ("TRUSTED_GENERAL", "CAUTION_LIMITED")
    assert key in by_pair, f"Expected {key} in {list(by_pair.keys())}"
    transition_count = by_pair[key]["count"]
    assert transition_count == 2, f"Expected 2 transitions, got {transition_count}"

    # Test 3: days parameter bounds
    r_empty = client.get("/api/verdict/trend?days=1")
    assert r_empty.status_code == 200

    # Test 4: auth failure on protected endpoint (none -- public endpoint)
    # Just verify the response shape is consistent
    for d in [3, 7, 30]:
        rd = client.get(f"/api/verdict/trend?days={d}")
        assert rd.status_code == 200
        jd = rd.json()
        assert "days" in jd and jd["days"] == d
        assert "series" in jd and isinstance(jd["series"], list)

    print("PASS")
    sys.exit(0)
