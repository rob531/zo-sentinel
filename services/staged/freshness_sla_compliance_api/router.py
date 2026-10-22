from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_freshness_sla_compliance as fetch_freshness_sla_compliance

router = APIRouter(prefix="/api")


@router.get("/freshness/sla")
def get_freshness_sla(session: Session = Depends(get_session)):
    return fetch_freshness_sla_compliance(session)