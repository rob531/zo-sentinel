from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_entity_detail

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/detail")
def entity_detail_view(server_id: str, db: Session = Depends(get_session)):
    return get_entity_detail(server_id, db)