from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

from .contract import SeverityRollupResponse
from .logic import get_severity_rollup

router = APIRouter()


@router.get(
    "/vuln/advisories/severity-rollup",
    response_model=SeverityRollupResponse,
)
def severity_rollup(
    session: Session = Depends(get_session),
) -> SeverityRollupResponse:
    return get_severity_rollup(session)