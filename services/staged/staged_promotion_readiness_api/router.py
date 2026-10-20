from fastapi import APIRouter, Depends
from sqlalchemy import text
from app.db import get_session
from .logic import get_staged_readiness

router = APIRouter(prefix="/api/internal", tags=["staged-readiness"])

@router.get("/staged-readiness")
async def staged_readiness(session=Depends(get_session)):
    return await get_staged_readiness(session)