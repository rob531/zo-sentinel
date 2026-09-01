from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_growth_snapshot

router = APIRouter(prefix="/api", tags=["registry_growth_snapshot"])


@router.get("/registry/growth-snapshot")
def growth_snapshot(session: Session = Depends(get_session)):
    return get_registry_growth_snapshot(session)