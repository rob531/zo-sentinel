from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_backlog

router = APIRouter()


@router.get("/scores/rescore-backlog")
def rescore_backlog(session: Session = Depends(get_session)):
    """
    Return the SLA backlog for MCP server re‑scoring.
    """
    return compute_backlog(session)