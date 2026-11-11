from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_model_version_mix


router = APIRouter(prefix="/api")


@router.get("/scoring/model-versions")
def scoring_model_version_mix(session: Session = Depends(get_session)):
    return get_model_version_mix(session)