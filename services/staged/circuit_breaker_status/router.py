from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_circuit_breaker_status

router = APIRouter(prefix="/api")


@router.get("/circuit-breaker/status")
async def get_status(session: Session = Depends(get_session)):
    return await get_circuit_breaker_status(session)