from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_linked_server_risk

router = APIRouter(prefix="/api", tags=["cve_linked_server_risk"])


@router.get("/cve/linked-servers")
def linked_servers(session: Session = Depends(get_session)):
    return get_linked_server_risk(session)