"""FastAPI router for the cadence_run_ledger_api service.

Provides a thin wrapper around the business logic in ``logic.py``.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

# Real application session – never a stub.
from app.db import get_session

# Business‑logic implementation (must exist in the same package).
from .logic import list_cadence_runs

router = APIRouter()


@router.get("/cadence/runs")
def get_cadence_runs(
    *,
    job: str | None = Query(default=None, description="Filter by job name"),
    limit: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of runs to return (clamped to 1‑500)",
    ),
    session: Session = Depends(get_session),
):
    """
    Return recent Cadence job runs.

    The heavy lifting (SQL query, shaping of the response) is performed in
    :func:`list_cadence_runs`.  This endpoint merely forwards the request
    parameters and the database session.
    """
    return list_cadence_runs(session=session, job=job, limit=limit)