from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import search_cves

router = APIRouter(prefix="/api")


@router.get("/cve/search")
def search_cves_endpoint(
    q: str = Query(..., alias="q"),
    session: Session = Depends(get_session),
):
    """Search CVEs by query string.

    Returns a JSON payload matching the contract defined in `services/_exemplar/logic.py`.
    """
    return search_cves(q, session)


__all__ = ["router"]