from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter()


@router.get("/api/servers/{server_id}/badge")
def get_server_badge(
    server_id: str,
    session: Session = Depends(get_session)
):
    from .logic import compute_badge
    return compute_badge(session, server_id)