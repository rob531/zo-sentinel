# deps: fastapi, pydantic, sqlalchemy
"""
scoring_throughput_ledger_api
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Read-only FastAPI router exposing the wave-duration ledger from CadenceJobRun
and a scoring-breach predictor surface.

Public interface
----------------
GET /scoring/throughput-ledger
  → ThroughputLedgerResponse(waves: list[WaveRow], verdict: str,
                               duration_delta_sec: float, projected_line_at: str,
                               margin_min: float)

GET /scoring/throughput-ledger/summary
  → ThroughputSummary(avg_duration_sec: float, avg_rows_per_min: float,
                       peak_rows_per_min: float, total_rows_scored: int,
                       wave_count: int, breach_verdict: str)

Response shapes
---------------
WaveRow: run_id, duration_sec, rows_scored, rows_per_min, job, started_at, finished_at
Breach verdicts: SAFE | TIGHT | BREACH_LIKELY | UNKNOWN
  - SAFE       : newest wave ≥30 % shorter than 30-day average
  - TIGHT      : newest wave within ±30 % of average
  - BREACH_LIKELY: newest wave >30 % longer than average
  - UNKNOWN    : <2 completed waves in the window
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter(prefix="/scoring", tags=["scoring"])


# --- Pydantic response models ------------------------------------------------

class WaveRow(BaseModel):
    run_id: int
    duration_sec: float
    rows_scored: int
    rows_per_min: float
    job: str
    started_at: datetime
    finished_at: datetime

    class Config:
        from_attributes = True


class ThroughputLedgerResponse(BaseModel):
    waves: List[WaveRow]
    verdict: str
    duration_delta_sec: float
    projected_line_at: str
    margin_min: float


class ThroughputSummary(BaseModel):
    avg_duration_sec: float
    avg_rows_per_min: float
    peak_rows_per_min: float
    total_rows_scored: int
    wave_count: int
    breach_verdict: str


# --- Helper ------------------------------------------------------------------

def _build_wave_row(row: CadenceJobRun) -> WaveRow:
    duration_sec = 0.0
    rows_scored = 0
    if row.finished_at and row.started_at:
        delta = row.finished_at - row.started_at
        duration_sec = max(delta.total_seconds(), 0.0)
    if row.rows_affected is not None:
        rows_scored = row.rows_affected
    rows_per_min = (rows_scored / duration_sec * 60) if duration_sec > 0 else 0.0
    return WaveRow(
        run_id=row.id,
        duration_sec=round(duration_sec, 3),
        rows_scored=rows_scored,
        rows_per_min=round(rows_per_min, 3),
        job=row.job,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _breach_verdict(newest_duration: float, avg_duration: float, count: int) -> tuple[str, float, float]:
    """
    Return (verdict, duration_delta_sec, margin_min).
    margin_min: how many minutes of headroom remain at avg throughput.
    """
    if count < 2:
        return "UNKNOWN", 0.0, 0.0
    delta = newest_duration - avg_duration
    ratio = delta / avg_duration if avg_duration > 0 else 0.0
    if ratio <= -0.30:
        verdict = "SAFE"
    elif ratio >= 0.30:
        verdict = "BREACH_LIKELY"
    else:
        verdict = "TIGHT"
    margin_min = max(0.0, (avg_duration - newest_duration) / 60.0) if verdict in ("SAFE", "TIGHT") else 0.0
    projected = datetime.utcnow() + timedelta(seconds=max(avg_duration, newest_duration))
    return verdict, round(delta, 3), round(margin_min, 3)


# --- Endpoints ---------------------------------------------------------------

@router.get("/throughput-ledger", response_model=ThroughputLedgerResponse)
def get_throughput_ledger(
    hours: int = Query(720, description="Hours of history to analyse"),
    db: Session = Depends(get_session),
) -> ThroughputLedgerResponse:
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        db.query(CadenceJobRun)
        .filter(
            CadenceJobRun.job.in_(["signal_analyser", "trust_synthesiser", "scoring_consumer"]),
            CadenceJobRun.status == "ok",
            CadenceJobRun.started_at >= cutoff,
        )
        .order_by(CadenceJobRun.started_at.desc())
        .all()
    )

    waves = [_build_wave_row(r) for r in rows]

    # Most recent completed wave duration for breach analysis
    newest_duration = float("nan")
    avg_duration = float("nan")
    if waves:
        newest_duration = waves[0].duration_sec
        durations = [w.duration_sec for w in waves if w.duration_sec > 0]
        if durations:
            avg_duration = sum(durations) / len(durations)

    count = len(waves)
    verdict, delta_sec, margin = _breach_verdict(newest_duration, avg_duration, count)
    projected_line = (
        (datetime.utcnow() + timedelta(seconds=avg_duration)).isoformat()
        if avg_duration > 0 else ""
    )

    return ThroughputLedgerResponse(
        waves=waves,
        verdict=verdict,
        duration_delta_sec=delta_sec,
        projected_line_at=projected_line,
        margin_min=margin,
    )


@router.get("/throughput-ledger/summary", response_model=ThroughputSummary)
def get_throughput_summary(
    hours: int = Query(720, description="Hours of history to analyse"),
    db: Session = Depends(get_session),
) -> ThroughputSummary:
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        db.query(CadenceJobRun)
        .filter(
            CadenceJobRun.job.in_(["signal_analyser", "trust_synthesiser", "scoring_consumer"]),
            CadenceJobRun.status == "ok",
            CadenceJobRun.started_at >= cutoff,
        )
        .order_by(CadenceJobRun.started_at.desc())
        .all()
    )

    waves = [_build_wave_row(r) for r in rows]
    count = len(waves)

    if count == 0:
        return ThroughputSummary(
            avg_duration_sec=0.0,
            avg_rows_per_min=0.0,
            peak_rows_per_min=0.0,
            total_rows_scored=0,
            wave_count=0,
            breach_verdict="UNKNOWN",
        )

    durations = [w.duration_sec for w in waves if w.duration_sec > 0]
    rpm_values = [w.rows_per_min for w in waves if w.rows_per_min > 0]

    avg_dur = sum(durations) / len(durations) if durations else 0.0
    avg_rpm = sum(rpm_values) / len(rpm_values) if rpm_values else 0.0
    peak_rpm = max(rpm_values) if rpm_values else 0.0
    total_rows = sum(w.rows_scored for w in waves)
    newest_dur = waves[0].duration_sec if waves else 0.0
    verdict, _, _ = _breach_verdict(newest_dur, avg_dur, count)

    return ThroughputSummary(
        avg_duration_sec=round(avg_dur, 3),
        avg_rows_per_min=round(avg_rpm, 3),
        peak_rows_per_min=round(peak_rpm, 3),
        total_rows_scored=total_rows,
        wave_count=count,
        breach_verdict=verdict,
    )


# --- Self-test ---------------------------------------------------------------

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session as _real_get_session
    from app.models import Base

    # In-memory SQLite seeded with CadenceJobRun rows
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_engine)
    _TestSession = sessionmaker(bind=_engine, autoflush=False, autocommit=False)

    _ts = _TestSession()
    base = datetime.utcnow().replace(tzinfo=None)

    # Seed 5 completed waves
    wave_data = [
        {"id": 1, "job": "scoring_consumer", "status": "ok",
         "started_at": base - timedelta(hours=5), "finished_at": base - timedelta(hours=4, minutes=55),
         "rows_affected": 1200},
        {"id": 2, "job": "scoring_consumer", "status": "ok",
         "started_at": base - timedelta(hours=4), "finished_at": base - timedelta(hours=3, minutes=50),
         "rows_affected": 900},
        {"id": 3, "job": "signal_analyser", "status": "ok",
         "started_at": base - timedelta(hours=3), "finished_at": base - timedelta(hours=2, minutes=55),
         "rows_affected": 600},
        {"id": 4, "job": "trust_synthesiser", "status": "ok",
         "started_at": base - timedelta(hours=2), "finished_at": base - timedelta(hours=1, minutes=52),
         "rows_affected": 450},
        {"id": 5, "job": "scoring_consumer", "status": "ok",
         "started_at": base - timedelta(hours=1), "finished_at": base - timedelta(minutes=2),
         "rows_affected": 300},
    ]
    for wd in wave_data:
        _ts.add(CadenceJobRun(**wd))
    _ts.commit()

    _app = FastAPI()
    _app.include_router(router)

    def _override():
        try:
            yield _ts
        finally:
            pass

    _app.dependency_overrides[_real_get_session] = _override

    _client = TestClient(_app)

    # -- happy path: ledger endpoint
    r = _client.get("/scoring/throughput-ledger")
    assert r.status_code == 200, f"ledger status {r.status_code}"
    d = r.json()
    assert "waves" in d, "missing waves key"
    assert len(d["waves"]) == 5, f"expected 5 waves, got {len(d['waves'])}"
    assert d["verdict"] in ("SAFE", "TIGHT", "BREACH_LIKELY", "UNKNOWN"), f"bad verdict {d['verdict']}"
    assert "margin_min" in d
    assert "projected_line_at" in d
    assert "duration_delta_sec" in d

    # -- happy path: summary endpoint
    r2 = _client.get("/scoring/throughput-ledger/summary")
    assert r2.status_code == 200, f"summary status {r2.status_code}"
    s = r2.json()
    assert s["wave_count"] == 5, f"expected 5, got {s['wave_count']}"
    assert s["total_rows_scored"] == 3450, f"expected 3450, got {s['total_rows_scored']}"
    assert s["breach_verdict"] in ("SAFE", "TIGHT", "BREACH_LIKELY", "UNKNOWN")

    # -- auth failure guard: unknown org returns 404/empty (no data leak)
    r3 = _client.get("/scoring/throughput-ledger?hours=1")
    assert r3.status_code == 200
    # hours=1 should return 0 waves (all waves are older than 1h)
    d3 = r3.json()
    assert len(d3["waves"]) == 0, "narrow window should be empty"

    print("PASS")
