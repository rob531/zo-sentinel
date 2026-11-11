from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_axis_missing_coverage

router = APIRouter(prefix="/api")


@router.get("/scores/axis-coverage")
def axis_missing_coverage(session: Session = Depends(get_session)):
    return get_axis_missing_coverage(session)