from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import audit_provenance

router = APIRouter(prefix="/api", tags=["vuln_link_provenance_audit"])


@router.get("/vuln-links/provenance-audit")
def get_provenance_audit(session: Session = Depends(get_session)):
    return audit_provenance(session)