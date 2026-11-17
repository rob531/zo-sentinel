from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore
from .logic import get_axis_scoring_version_drift

router = APIRouter(prefix="/api")


@router.get("/scores/version-drift")
def axis_scoring_version_drift(
    days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    effective_days = max(1, min(365, days))
    return get_axis_scoring_version_drift(session, effective_days)