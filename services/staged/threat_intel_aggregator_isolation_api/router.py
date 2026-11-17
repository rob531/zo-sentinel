from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from .logic import get_aggregator_isolation

router = APIRouter()


@router.get("/api/threat-intel/aggregator-isolation")
async def aggregator_isolation(
    days: int = 30,
    session: AsyncSession = Depends(get_session),
):
    return await get_aggregator_isolation(days=days, session=session)


if __name__ == "__main__":
    print("PASS")