# services/staged/registry_health_dashboard_api/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_registry_health_stats
from .schemas import RegistryHealthResponse

router = APIRouter(prefix="/api", tags=["registry_health"])

@router.get("/registry/health", response_model=list[RegistryHealthResponse])
def registry_health(session: Session = Depends(get_session)):
    return get_registry_health_stats(session)