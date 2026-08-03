from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_severity_rollup
from .models import SeverityRollupResponse

router = APIRouter()


@router.get(
    "/api/cves/severity-rollup",
    response_model=SeverityRollupResponse,
    tags=["cve_severity_rollup"],
    summary="Get CVE severity rollup across all scored servers",
)
def severity_rollup(
    days: Optional[int] = None,
    session: Session = Depends(get_session),
) -> SeverityRollupResponse:
    return get_severity_rollup(session=session, days=days)