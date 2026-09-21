from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cve_facet

router = APIRouter(prefix="/api", tags=["cve_facet_compile_v3"])


@router.get("/cve/facet")
def cve_facet_endpoint(session: Session = Depends(get_session)):
    """
    Thin wrapper that delegates to the service logic.
    """
    return get_cve_facet(session)