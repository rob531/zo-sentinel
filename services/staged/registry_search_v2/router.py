from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_registry_search

router = APIRouter(prefix="/api/registry", tags=["registry_search_v2"])


@router.get("/search")
def registry_search(
    q: str = Query("", alias="q"),
    tier: str | None = Query(None, alias="tier"),
    source: str | None = Query(None, alias="source"),
    sort: str = Query("trust_score", alias="sort"),
    page: int = Query(1, ge=1, alias="page"),
    limit: int = Query(20, ge=1, le=100, alias="limit"),
    db: Session = Depends(get_session),
):
    return get_registry_search(
        db=db,
        q=q,
        tier=tier,
        source=source,
        sort=sort,
        page=page,
        limit=limit,
    )