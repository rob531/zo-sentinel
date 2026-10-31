from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun

from .contract import CadenceSlaResponse
from .logic import compute_cadence_sla

router = APIRouter(prefix="/api")


@router.get("/cadence/sla", response_model=CadenceSlaResponse)
def get_cadence_sla(session: Session = Depends(get_session)) -> dict:
    rows = [
        {
            "job": run.job,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "rows_affected": run.rows_affected,
            "detail": run.detail,
        }
        for run in session.query(CadenceJobRun).all()
    ]
    return compute_cadence_sla(rows, datetime.now(timezone.utc).isoformat())