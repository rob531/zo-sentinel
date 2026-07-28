from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_entity_report

router = APIRouter(prefix="/api")


@router.get("/report/entity/{server_id}")
def entity_report(server_id: int, session: Session = Depends(get_session)):
    return get_entity_report(server_id, session)