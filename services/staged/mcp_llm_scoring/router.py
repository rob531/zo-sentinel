from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_scores, ServerScoreResponse

router = APIRouter()


@router.get(
    "/servers/{server_id}/scores",
    response_model=ServerScoreResponse,
    tags=["mcp_llm_scoring"],
)
def server_scores(
    server_id: int,
    session: Session = Depends(get_session),
) -> ServerScoreResponse:
    """
    Retrieve aggregated LLM axis scores for a given MCP server and calculate its overall risk tier.
    """
    try:
        return get_server_scores(server_id, session)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))