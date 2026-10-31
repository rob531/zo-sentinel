from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import RegistrySourceTierMixResponse, get_registry_source_tier_mix

router = APIRouter(prefix="/api")


@router.get(
    "/registry/source-tier-mix",
    response_model=RegistrySourceTierMixResponse,
    status_code=200,
    summary="Risk tier distribution per registry source",
)
def registry_source_tier_mix_endpoint(
    session: Session = Depends(get_session),
):
    """Return aggregated tier distribution for each registry source."""
    result = get_registry_source_tier_mix(session)
    if not result or not result.sources:
        raise HTTPException(status_code=404, detail="No data")
    return result


__all__ = ["router"]