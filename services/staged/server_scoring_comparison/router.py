from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import ComparisonResponse, get_comparison

router = APIRouter(prefix="/api")


@router.get(
    "/scoring/comparison/{server_id}",
    response_model=ComparisonResponse,
    name="server_scoring_comparison",
)
def server_scoring_comparison(
    server_id: int, db: Session = Depends(get_session)
) -> ComparisonResponse:
    """
    Retrieve a scoring comparison for a given server.

    Parameters
    ----------
    server_id: int
        The identifier of the server to compare.
    db: Session
        Database session provided by FastAPI dependency injection.

    Returns
    -------
    ComparisonResponse
        Pydantic model containing the comparison data.
    """
    return get_comparison(server_id, db)