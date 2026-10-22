from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_source_scoring_lag

router = APIRouter(prefix="/api", tags=["registry_source_scoring_lag"])

@router.get("/registry/sources/scoring-lag")
def registry_source_scoring_lag(session: Session = Depends(get_session)):
    return get_registry_source_scoring_lag(session)