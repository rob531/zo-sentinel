from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .contract import RegistryIngestFreshnessResponse
from .logic import get_registry_ingest_freshness

router = APIRouter(
    prefix="/api",
    tags=["registry-ingest-freshness"],
)


@router.get(
    "/registry/ingest-freshness",
    response_model=RegistryIngestFreshnessResponse,
)
def registry_ingest_freshness(
    hours: int = Query(24),
    session: Session = Depends(get_session),
):
    bounded_hours = max(1, min(720, hours))
    return get_registry_ingest_freshness(
        session=session,
        hours=bounded_hours,
    )


__all__ = ["router"]