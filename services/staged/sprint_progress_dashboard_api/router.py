from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_sprint_progress, SprintProgressResponse

router = APIRouter(prefix="/api", tags=["Sprint Progress Dashboard"])


@router.get(
    "/scoring/sprint-progress",
    response_model=SprintProgressResponse,
    summary="Get sprint progress scoring summary",
)
def sprint_progress_endpoint(session: Session = Depends(get_session)):
    """
    Retrieve sprint progress statistics for the current 7‑day sprint window.

    The heavy lifting is performed in `services.staged.sprint_progress_dashboard_api.logic`.
    """
    return get_sprint_progress(session)