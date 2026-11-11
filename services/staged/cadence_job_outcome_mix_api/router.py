from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cadence_job_outcome_mix

router = APIRouter(prefix="/api")


@router.get("/cadence/jobs/outcome-mix")
def outcome_mix(session: Session = Depends(get_session)):
    return get_cadence_job_outcome_mix(session)