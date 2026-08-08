from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_cve_severity_rollup, SeverityRollupResponse

router = APIRouter(prefix="/api")


@router.get(
    "/cve/severity-rollup",
    response_model=SeverityRollupResponse,
    name="cve_severity_rollup",
)
def cve_severity_rollup_endpoint(session: Session = Depends(get_session)):
    """
    Retrieve a roll‑up of CVE severities across servers.

    The heavy lifting is performed in `services.staged.cve_severity_rollup.logic`.
    """
    return get_cve_severity_rollup(session)