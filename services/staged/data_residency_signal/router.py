# services/staged/data_residency_signal/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_server_data_residency

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/data-residency")
def get_data_residency(
    server_id: str,
    session: Session = Depends(get_session),
):
    return get_server_data_residency(session, server_id)