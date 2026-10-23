"""GET /scoring/waves/summary: totals over the scoring-wave cadence job runs.

Reads `cadence_job_runs` from Fly Postgres through the injected session. Until
FU-667 (#6985) this POSTed SQL to http://127.0.0.1:8772/query -- the factory
DuckDB write_service, which runs on the ZoComputer host and is not in the Fly
image -- so on Fly the route could only 500 (Errno 111), from its 2026-09-28
mount (#5689) on. The SQL it sent was already Postgres
(`detail->>'cost_usd'`): the table it wanted is the app's own.
"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

router = APIRouter()

class WaveSummary(BaseModel):
    total_waves: int
    servers_scored: int
    avg_per_wave: float
    last_wave_at: Optional[str]
    error_count: int
    cost_estimate_usd: Optional[float]

def _cost_usd(detail) -> Optional[float]:
    if not isinstance(detail, dict):
        return None
    try:
        return float(detail["cost_usd"])
    except (KeyError, TypeError, ValueError):
        return None

@router.get("/scoring/waves/summary", response_model=WaveSummary)
def scoring_wave_summary(db: Session = Depends(get_session)):
    # One row per job run, so the wave subset is small: aggregate in Python.
    # `detail` is JSON and `->>` is Postgres-only, while dev/CI run sqlite.
    runs = db.execute(
        select(CadenceJobRun.rows_affected, CadenceJobRun.finished_at,
               CadenceJobRun.status, CadenceJobRun.detail)
        .where(or_(CadenceJobRun.job.startswith("score_wave", autoescape=True),
                   CadenceJobRun.job == "scoring_batch"))
    ).all()

    affected = [r.rows_affected for r in runs if r.rows_affected is not None]
    finished = [r.finished_at for r in runs if r.finished_at is not None]
    costs = [c for c in (_cost_usd(r.detail) for r in runs) if c is not None]

    return WaveSummary(
        total_waves=len(runs),
        servers_scored=sum(affected),
        avg_per_wave=sum(affected) / len(affected) if affected else 0.0,
        last_wave_at=max(finished).isoformat() if finished else None,
        error_count=sum(1 for r in runs if r.status == "failed"),
        cost_estimate_usd=round(sum(costs), 2) if costs else None,
    )
