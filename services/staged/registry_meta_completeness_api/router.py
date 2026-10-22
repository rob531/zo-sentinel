from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_meta_completeness

router = APIRouter(prefix="/api")


@router.get("/registry/meta-completeness")
async def registry_meta_completeness(
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
):
    result = get_registry_meta_completeness(session, limit)
    if hasattr(result, "__await__"):
        return await result
    return result