from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_risk_snapshot

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/risk-snapshot")
def server_risk_snapshot(
    server_id: str,
    session: Session = Depends(get_session),
):
    return get_risk_snapshot(session, server_id)