from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_verdict_age_buckets

router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/registry/verdict-age-buckets")
async def verdict_age_buckets(
    bucket_hours: int = 24,
    session: AsyncSession = Depends(get_session),
):
    return await get_verdict_age_buckets(
        session=session,
        bucket_hours=bucket_hours,
    )