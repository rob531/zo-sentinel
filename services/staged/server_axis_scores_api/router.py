from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_axis_scores

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/axis-scores")
def read_axis_scores(
    server_id: int,
    db: Session = Depends(get_session),
):
    """
    Retrieve the latest axis scores for a given server.

    The heavy‑lifting is performed in `services.staged.server_axis_scores_api.logic.get_axis_scores`,
    which queries the `McpLlmAxisScore` table and assembles the response payload.
    """
    return get_axis_scores(server_id, db)