from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry
from .logic import TrustScoreBandResponse, get_trust_score_bands

router = APIRouter(prefix="/api", tags=["trust-score-band-api"])


@router.get(
    "/registry/trust-score-bands",
    response_model=TrustScoreBandResponse,
)
async def list_trust_score_bands(
    session: AsyncSession = Depends(get_session),
) -> TrustScoreBandResponse:
    return await get_trust_score_bands(session)