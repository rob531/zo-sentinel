from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_attestation_summary

router = APIRouter(prefix="/api")


@router.get("/attestations/summary")
def attestation_summary(session: Session = Depends(get_session)):
    return get_attestation_summary(session)