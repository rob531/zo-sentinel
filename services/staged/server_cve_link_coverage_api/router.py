from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_vuln_link_coverage

router = APIRouter(prefix="/api", tags=["vulnerability"])


@router.get("/vuln/coverage")
def get_vuln_link_coverage_endpoint(
    session: Session = Depends(get_session),
) -> dict:
    return get_vuln_link_coverage(session)