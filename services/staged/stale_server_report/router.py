# services/staged/stale_server_report/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_stale_servers

router = APIRouter(prefix="/api", tags=["stale_server_report"])


@router.get("/servers/stale")
def get_stale_servers_endpoint(days: int = 30, session: Session = Depends(get_session)):
    result = get_stale_servers(session=session, days=days)
    return result