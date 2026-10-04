# deps: fastapi, pydantic, sqlalchemy
"""Scoring Wave Cost Ledger API.

GET /api/scoring/wave-cost-ledger
  Returns CadenceJobRun rows whose job name matches scoring/score keywords,
  with duration and rows_affected per wave, newest-first.

GET /api/scoring/wave-cost-ledger/{wave_id}
  Returns a single wave by CadenceJobRun.id.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app Postgres via get_session + CadenceJobRun + McpLlmAxisScore.
"""
from __future__ import annotations

import os
import sys as _sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in _sys.path:
    _sys.path.insert(0, _repo_root)

import sys
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, CadenceJobRun, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["mcp_scoring_wave_cost_ledger_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class WaveEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    status: Optional[str] = None
    rows_affected: Optional[int] = None
    detail: Optional[str] = None
    # enriched fields
    server_count: int = 0
    axis_count: int = 0
    model_versions: List[str] = []


class WaveLedgerResponse(BaseModel):
    waves: List[WaveEntry] = []
    total: int = 0
    limit: int = 50
    offset: int = 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

SCORING_KEYWORDS = ("scoring", "score", "llm", "axis", "verdict", "wave")


def _is_scoring_job(job_name: str) -> bool:
    lower = job_name.lower()
    return any(kw in lower for kw in SCORING_KEYWORDS)


def _enrich_wave(session: Session, wave: CadenceJobRun) -> WaveEntry:
    """Augment a CadenceJobRun row with McpLlmAxisScore aggregates."""
    duration = None
    if wave.started_at and wave.finished_at:
        duration = (wave.finished_at - wave.started_at).total_seconds()

    # Query axis scores scoped to this wave's time window
    axis_q = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.scored_at >= wave.started_at
    )
    if wave.finished_at:
        axis_q = axis_q.filter(McpLlmAxisScore.scored_at <= wave.finished_at)

    axis_scores = axis_q.all()

    server_ids = {s.server_id for s in axis_scores}
    model_versions = sorted({s.model_version for s in axis_scores if s.model_version})

    return WaveEntry(
        id=wave.id,
        job=wave.job,
        started_at=wave.started_at,
        finished_at=wave.finished_at,
        duration_seconds=duration,
        status=wave.status,
        rows_affected=wave.rows_affected,
        detail=wave.detail,
        server_count=len(server_ids),
        axis_count=len(axis_scores),
        model_versions=model_versions,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/scoring/wave-cost-ledger", response_model=WaveLedgerResponse)
def list_wave_cost_ledger(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    job_prefix: Optional[str] = Query(
        default=None,
        description="Filter by job name prefix (e.g. 'scoring_', 'score_')",
    ),
    db: Session = Depends(get_session),
) -> WaveLedgerResponse:
    """Return scoring job waves with cost/performance metrics, paginated."""
    # Count total
    total_q = db.query(func.count(CadenceJobRun.id))

    # Filter to scoring jobs
    scoring_filter = or_(
        func.lower(CadenceJobRun.job).contains(kw) for kw in SCORING_KEYWORDS
    )
    total_q = total_q.filter(scoring_filter)
    if job_prefix:
        total_q = total_q.filter(CadenceJobRun.job.ilike(f"{job_prefix}%"))

    total = total_q.scalar() or 0

    # Fetch rows
    rows_q = (
        db.query(CadenceJobRun)
        .filter(scoring_filter)
        .order_by(CadenceJobRun.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if job_prefix:
        rows_q = rows_q.filter(CadenceJobRun.job.ilike(f"{job_prefix}%"))

    waves = [_enrich_wave(db, row) for row in rows_q.all()]

    return WaveLedgerResponse(
        waves=waves,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/scoring/wave-cost-ledger/{wave_id}", response_model=WaveEntry)
def get_wave_cost_ledger_item(
    wave_id: int,
    db: Session = Depends(get_session),
) -> WaveEntry:
    """Return a single wave by CadenceJobRun.id."""
    row = db.query(CadenceJobRun).filter(CadenceJobRun.id == wave_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Wave {wave_id} not found")
    if not _is_scoring_job(row.job):
        raise HTTPException(status_code=404, detail=f"Wave {wave_id} is not a scoring job")
    return _enrich_wave(db, row)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    _now = datetime.utcnow()

    # Seed scoring and non-scoring rows
    with _TS() as _sess:
        # Scoring waves
        for i in range(1, 4):
            run = CadenceJobRun(
                id=i,
                job=f"scoring_wave_{i}",
                status="completed",
                started_at=_now - timedelta(hours=i * 3),
                finished_at=_now - timedelta(hours=i * 3 - 1),
                rows_affected=100 * i,
                detail=f"wave {i} complete",
            )
            _sess.add(run)

        # Non-scoring row -- should be excluded
        _sess.add(CadenceJobRun(
            id=99,
            job="backup_cleanup",
            status="completed",
            started_at=_now - timedelta(hours=1),
            finished_at=_now,
            rows_affected=0,
            detail="not a scoring job",
        ))
        _sess.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        s = _TS()
        try:
            yield s
        finally:
            s.close()

    _that_app.dependency_overrides[get_session] = _override_session
    _c = TestClient(_that_app)

    # Happy path: list returns only scoring waves (excludes backup_cleanup)
    resp = _c.get("/api/scoring/wave-cost-ledger")
    assert resp.status_code == 200, f"list failed: {resp.status_code} {resp.text}"
    payload = resp.json()
    assert "waves" in payload, f"Missing waves key: {payload}"
    assert payload["total"] == 3, f"Expected 3 waves, got {payload['total']}: {[w['job'] for w in payload['waves']]}"
    assert len(payload["waves"]) == 3
    for w in payload["waves"]:
        assert w["rows_affected"] >= 0, f"Negative rows_affected: {w}"
        assert isinstance(w["duration_seconds"], (float, type(None))), f"duration_seconds type wrong: {type(w['duration_seconds'])}"

    # Pagination: limit=2
    resp2 = _c.get("/api/scoring/wave-cost-ledger?limit=2&offset=0")
    assert resp2.status_code == 200
    assert len(resp2.json()["waves"]) == 2
    assert resp2.json()["total"] == 3

    # Single wave endpoint: valid id
    resp3 = _c.get("/api/scoring/wave-cost-ledger/1")
    assert resp3.status_code == 200, f"get wave 1 failed: {resp3.status_code} {resp3.text}"
    item = resp3.json()
    assert item["id"] == 1
    assert "scoring_wave" in item["job"]
    assert item["rows_affected"] == 100

    # Single wave endpoint: non-scoring id returns 404
    resp4 = _c.get("/api/scoring/wave-cost-ledger/99")
    assert resp4.status_code == 404, f"Expected 404 for non-scoring wave 99, got {resp4.status_code}"

    # Single wave endpoint: missing id returns 404
    resp5 = _c.get("/api/scoring/wave-cost-ledger/9999")
    assert resp5.status_code == 404

    # job_prefix filter
    resp6 = _c.get("/api/scoring/wave-cost-ledger?job_prefix=scoring_")
    assert resp6.status_code == 200
    for w in resp6.json()["waves"]:
        assert w["job"].startswith("scoring_"), f"Unexpected job: {w['job']}"

    print("PASS")
